"""chart_loader: carga los CSV del chart semanal que aparecen en la carpeta de entrada.

Corre cada 15 minutos con cron, y una vez al iniciar el contenedor batch:

  1. Lista los CSV de data/landing/charts/. Los descargo a mano con el
     botón oficial de Spotify Charts: los términos prohíben el scraping.
  2. Calcula el sha256 de cada archivo y omite los que ya están en
     etl_loaded_file (cargados, reemplazados o rechazados). Así, correrlo
     dos veces no duplica nada.
  3. Valida el nombre (regional-mx-weekly-AAAA-MM-DD.csv, con la fecha en
     jueves), el encabezado y las filas. Si algo falla, registra el
     archivo como rechazado, con el motivo, y no carga nada de él.
     Se aceptan hasta 5 lugares faltantes: Spotify borra de los charts
     históricos las canciones que retira de la plataforma (el CSV del
     2026-02-05 no trae el lugar 34). La semana se carga con un WARNING
     y los lugares faltantes quedan anotados en etl_loaded_file.detail.
  4. Copia el archivo a la zona cruda (data/raw/charts/) con parte del
     sha256 en el nombre. La copia no cambia aunque se borre o reemplace
     el archivo de entrada.
  5. En UNA transacción:
       * si la semana ya estaba cargada con otro archivo, la borra de
         fact_chart_week y marca el archivo anterior como reemplazado;
       * agrega los artistas y canciones nuevos a dim_artist, dim_song y
         song_artist, y actualiza los existentes (SCD tipo 1);
       * inserta las 200 filas en fact_chart_week;
       * registra el archivo en etl_loaded_file.
     O queda todo el archivo o no queda nada, y la siguiente corrida lo
     vuelve a intentar.
  6. Deja una línea de inicio y otra de fin en logs/chart_loader.log.

Si llegan varios archivos nuevos a la vez (por ejemplo, las 12 semanas de
historia), los carga de la semana más vieja a la más nueva. Los datos
descriptivos de una canción (título, sello, artistas) solo se
sobrescriben con un archivo de una semana igual o posterior a la última
en que apareció; un archivo viejo cargado tarde no los regresa a una
versión anterior.

Una canción puede tener varias URI: Spotify a veces le cambia la URI pero
mantiene su previous_rank. Cada URI se guarda en song_uri apuntando a su
canción (ver transform/song_identity.py).
"""

import csv
import hashlib
import io
import logging
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg

from common.jobs import LANDING_DIR, RAW_DIR, pg_params, setup_logging, single_run
from transform.normalize import normalize_artist, normalize_label, normalize_title, split_artists
from transform.song_identity import find_previous_song, find_reentry_song, reconcile

JOB = "chart_loader"
CHARTS_LANDING = LANDING_DIR / "charts"
CHARTS_RAW = RAW_DIR / "charts"

EXPECTED_HEADER = ["rank", "uri", "artist_names", "track_name", "source",
                   "peak_rank", "previous_rank", "weeks_on_chart", "streams"]
CHART_ROWS = 200
MAX_MISSING_RANKS = 5  # canciones que Spotify retiró del chart histórico
THURSDAY = 3  # date.weekday(): lunes = 0
FILE_NAME_RE = re.compile(r"^regional-mx-weekly-(\d{4}-\d{2}-\d{2})(?: ?\(\d+\))?\.csv$")
URI_RE = re.compile(r"^spotify:track:[0-9A-Za-z]{22}$")
MAX_REPORTED_ERRORS = 5


class InvalidFile(Exception):
    """El archivo no es un chart semanal de México con el formato esperado."""


@dataclass(frozen=True)
class ChartRow:
    rank: int
    uri: str
    artists: tuple[str, ...]
    track_name: str
    source: str | None
    peak_rank: int
    previous_rank: int | None
    weeks_on_chart: int
    streams: int


def week_key(week_end: date) -> int:
    """date_key (AAAAMMDD) del jueves que identifica la semana."""
    return int(week_end.strftime("%Y%m%d"))


def parse_week(file_name: str) -> date:
    """Fecha de fin de semana (jueves) a partir del nombre del archivo."""
    match = FILE_NAME_RE.match(file_name)
    if not match:
        raise InvalidFile("el nombre no es regional-mx-weekly-AAAA-MM-DD.csv")
    try:
        week_end = date.fromisoformat(match.group(1))
    except ValueError:
        raise InvalidFile(f"la fecha {match.group(1)} del nombre no existe")
    if week_end.weekday() != THURSDAY:
        raise InvalidFile(f"la fecha {week_end} del nombre no es jueves; "
                          "el chart semanal termina en jueves")
    return week_end


def _int(value: str, field: str, low: int, high: int | None = None) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{field} no es un entero ({value!r})")
    if number < low or (high is not None and number > high):
        rango = f"{low} a {high}" if high is not None else f"al menos {low}"
        raise ValueError(f"{field} = {number} fuera de rango ({rango})")
    return number


def parse_row(raw: dict) -> ChartRow:
    """Valida una fila del CSV y la convierte a tipos de Python."""
    uri = raw["uri"].strip()
    if not URI_RE.match(uri):
        raise ValueError(f"uri inválida ({uri!r})")
    artists = tuple(split_artists(raw["artist_names"]))
    if not artists:
        raise ValueError("artist_names vacío")
    track_name = raw["track_name"].strip()
    if not track_name:
        raise ValueError("track_name vacío")
    previous = raw["previous_rank"].strip()
    previous_rank = None if previous == "-1" else _int(previous, "previous_rank", 1, CHART_ROWS)
    return ChartRow(
        rank=_int(raw["rank"], "rank", 1, CHART_ROWS),
        uri=uri,
        artists=artists,
        track_name=track_name,
        source=raw["source"].strip() or None,
        peak_rank=_int(raw["peak_rank"], "peak_rank", 1, CHART_ROWS),
        previous_rank=previous_rank,
        weeks_on_chart=_int(raw["weeks_on_chart"], "weeks_on_chart", 1),
        streams=_int(raw["streams"], "streams", 0),
    )


def missing_ranks(rows: list[ChartRow], chart_size: int = CHART_ROWS) -> list[int]:
    """Lugares del 1 al chart_size que no vienen en el archivo."""
    return sorted(set(range(1, chart_size + 1)) - {r.rank for r in rows})


def parse_chart(content: bytes, expected_rows: int = CHART_ROWS,
                max_missing: int = MAX_MISSING_RANKS) -> list[ChartRow]:
    """Valida el contenido completo del CSV. Lanza InvalidFile con los primeros errores.

    Acepta entre expected_rows - max_missing y expected_rows filas, con los
    rank sin repetir; los lugares faltantes se consultan con missing_ranks().
    """
    try:
        text = content.decode("utf-8-sig")  # el CSV de Spotify trae BOM
    except UnicodeDecodeError:
        raise InvalidFile("el archivo no está en UTF-8")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header != EXPECTED_HEADER:
        raise InvalidFile(f"encabezado inesperado: {header}")

    rows, errors = [], []
    for line_number, values in enumerate(reader, start=2):
        if not values:
            continue
        if len(values) != len(EXPECTED_HEADER):
            errors.append(f"línea {line_number}: {len(values)} columnas en lugar de "
                          f"{len(EXPECTED_HEADER)}")
            continue
        try:
            rows.append(parse_row(dict(zip(EXPECTED_HEADER, values))))
        except ValueError as e:
            errors.append(f"línea {line_number}: {e}")

    if not errors:
        if not expected_rows - max_missing <= len(rows) <= expected_rows:
            errors.append(f"tiene {len(rows)} filas; se esperan {expected_rows} "
                          f"(se toleran hasta {max_missing} faltantes)")
        ranks = Counter(r.rank for r in rows)
        repeated_ranks = sorted(k for k, n in ranks.items() if n > 1)
        if repeated_ranks:
            errors.append(f"rank repetido: {repeated_ranks[:MAX_REPORTED_ERRORS]}")
        uris = Counter(r.uri for r in rows)
        repeated_uris = sorted(k for k, n in uris.items() if n > 1)
        if repeated_uris:
            errors.append(f"uri repetida: {repeated_uris[:MAX_REPORTED_ERRORS]}")
    if errors:
        extra = f" (y {len(errors) - MAX_REPORTED_ERRORS} más)" if len(errors) > MAX_REPORTED_ERRORS else ""
        raise InvalidFile("; ".join(errors[:MAX_REPORTED_ERRORS]) + extra)
    return sorted(rows, key=lambda r: r.rank)


def write_raw(content: bytes, week_end: date, sha256: str) -> str:
    """Copia el archivo a la zona cruda (escritura temporal + renombrado).

    Regresa la ruta relativa a data/raw/. Si ya existe (una corrida
    anterior la copió y falló después), no la vuelve a escribir.
    """
    CHARTS_RAW.mkdir(parents=True, exist_ok=True)
    name = f"regional-mx-weekly-{week_end.isoformat()}_{sha256[:12]}.csv"
    target = CHARTS_RAW / name
    if not target.exists():
        tmp = target.with_suffix(".tmp")
        with tmp.open("wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    return f"charts/{name}"


def record_rejected(conn: psycopg.Connection, file_name: str, sha256: str,
                    week_end: date | None, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO etl_loaded_file (file_sha256, file_name, chart_week_end, status, detail)
        VALUES (%s, %s, %s, 'rejected', %s)
        ON CONFLICT (file_sha256) DO NOTHING
        """,
        (sha256, file_name, week_end, reason[:2000]),
    )


def load_file(conn: psycopg.Connection, file_name: str, sha256: str, week_end: date,
              rows: list[ChartRow], raw_path: str, log: logging.Logger | None = None) -> dict:
    """Carga un archivo ya validado en una sola transacción. Regresa un resumen."""
    wk = week_key(week_end)
    with conn.transaction(), conn.cursor() as cur:
        # 1. ¿La semana ya estaba cargada con otro archivo? Se reemplaza completa.
        cur.execute(
            "SELECT file_name FROM etl_loaded_file "
            "WHERE chart_week_end = %s AND status = 'loaded' FOR UPDATE",
            (week_end,),
        )
        previous = cur.fetchone()
        if previous:
            cur.execute(
                "UPDATE etl_loaded_file SET status = 'replaced', detail = %s "
                "WHERE chart_week_end = %s AND status = 'loaded'",
                (f"reemplazado por {file_name}", week_end),
            )
            cur.execute("DELETE FROM fact_chart_week WHERE week_key = %s", (wk,))

        # ¿Es esta la semana más reciente cargada? Solo entonces se
        # sobrescribe cómo se escribe el nombre de un artista.
        cur.execute("SELECT COALESCE(max(week_key), 0) FROM fact_chart_week")
        is_newest_week = wk >= cur.fetchone()[0]

        # 2. Artistas: nombre normalizado → como aparece en este archivo.
        artists: dict[str, str] = {}
        for row in rows:
            for name in row.artists:
                artists.setdefault(normalize_artist(name), name)
        cur.execute(
            "SELECT artist_name_norm FROM dim_artist WHERE artist_name_norm = ANY(%s)",
            (list(artists),),
        )
        existing_artists = {r[0] for r in cur.fetchall()}
        new_artists = [(name, norm) for norm, name in artists.items() if norm not in existing_artists]
        if new_artists:
            cur.executemany(
                "INSERT INTO dim_artist (artist_name, artist_name_norm) VALUES (%s, %s)",
                new_artists,
            )
        if is_newest_week:
            cur.executemany(
                "UPDATE dim_artist SET artist_name = %s, updated_at = now() "
                "WHERE artist_name_norm = %s AND artist_name <> %s",
                [(name, norm, name) for norm, name in artists.items() if norm in existing_artists],
            )
        cur.execute(
            "SELECT artist_name_norm, artist_key FROM dim_artist WHERE artist_name_norm = ANY(%s)",
            (list(artists),),
        )
        artist_key = dict(cur.fetchall())

        # 3. Canciones: a qué song_key pertenece cada URI (ver transform/song_identity.py).
        def main_artist(row: ChartRow) -> int:
            return artist_key[normalize_artist(row.artists[0])]

        def song_values(row: ChartRow) -> tuple:
            return (row.track_name, normalize_title(row.track_name), main_artist(row),
                    row.source, normalize_label(row.source))

        cur.execute("SELECT spotify_uri, song_key FROM song_uri WHERE spotify_uri = ANY(%s)",
                    ([r.uri for r in rows],))
        song_key: dict[str, int] = dict(cur.fetchall())

        # 3a. URI nuevas de una canción que ya existía con otra URI: su
        #     previous_rank lleva a la misma canción en la semana anterior,
        #     o es un reingreso que sigue la cuenta de weeks_on_chart.
        prev_wk = week_key(week_end - timedelta(days=7))
        used = set(song_key.values())
        relinked: list[tuple[ChartRow, str]] = []
        for r in rows:
            if r.uri in song_key:
                continue
            title_norm = normalize_title(r.track_name)
            if r.previous_rank is not None:
                key = find_previous_song(cur, prev_wk, r.previous_rank, title_norm, main_artist(r))
                reason = "previous_rank"
            else:
                key = find_reentry_song(cur, wk, r.weeks_on_chart, title_norm, main_artist(r))
                reason = "reentry"
            if key is not None and key not in used:
                song_key[r.uri] = key
                used.add(key)
                relinked.append((r, reason))
        if relinked:
            cur.executemany(
                "INSERT INTO song_uri (spotify_uri, song_key, link_reason) VALUES (%s, %s, %s)",
                [(r.uri, song_key[r.uri], reason) for r, reason in relinked],
            )

        # 3b. Canciones nuevas.
        new_songs = [r for r in rows if r.uri not in song_key]
        if new_songs:
            cur.executemany(
                """
                INSERT INTO dim_song (spotify_uri, track_name, track_name_norm, main_artist_key,
                                      label, label_norm, first_seen_week)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [(r.uri, *song_values(r), wk) for r in new_songs],
            )
            cur.execute("SELECT spotify_uri, song_key FROM dim_song WHERE spotify_uri = ANY(%s)",
                        ([r.uri for r in new_songs],))
            song_key.update(cur.fetchall())
            cur.executemany(
                "INSERT INTO song_uri (spotify_uri, song_key, link_reason) VALUES (%s, %s, 'original')",
                [(r.uri, song_key[r.uri]) for r in new_songs],
            )

        if len({song_key[r.uri] for r in rows}) != len(rows):
            raise ValueError("dos URI del archivo pertenecen a la misma canción en song_uri; "
                             "hay que revisar esa tabla a mano")

        # 3c. Canciones que ya existían. last_week = última semana en que
        #     aparecían (sin contar esta, que se borró si era un reemplazo).
        new_uris = {r.uri for r in new_songs}
        existing = [r for r in rows if r.uri not in new_uris]
        cur.execute(
            "SELECT song_key, max(week_key) FROM fact_chart_week WHERE song_key = ANY(%s) GROUP BY 1",
            ([song_key[r.uri] for r in existing],),
        )
        last_week = dict(cur.fetchall())
        # Esta semana es la más reciente de la canción: se sobrescriben sus
        # datos descriptivos (SCD tipo 1).
        newer = [r for r in existing if wk >= last_week.get(song_key[r.uri], 0)]
        if newer:
            cur.executemany(
                """
                UPDATE dim_song
                SET track_name = %s, track_name_norm = %s, main_artist_key = %s,
                    label = %s, label_norm = %s,
                    first_seen_week = LEAST(first_seen_week, %s), updated_at = now()
                WHERE song_key = %s
                  AND (track_name, track_name_norm, main_artist_key, label, label_norm, first_seen_week)
                      IS DISTINCT FROM (%s, %s, %s, %s, %s, LEAST(first_seen_week, %s))
                """,
                [(*song_values(r), wk, song_key[r.uri], *song_values(r), wk) for r in newer],
            )
        # Las demás (este archivo es más viejo): solo puede cambiar first_seen_week.
        older = [song_key[r.uri] for r in existing if wk < last_week.get(song_key[r.uri], 0)]
        if older:
            cur.execute(
                "UPDATE dim_song SET first_seen_week = %s, updated_at = now() "
                "WHERE song_key = ANY(%s) AND first_seen_week > %s",
                (wk, older, wk),
            )

        # 4. Puente canción–artista de las canciones nuevas y las sobrescritas.
        refreshed = new_songs + newer
        if refreshed:
            refreshed_keys = [song_key[r.uri] for r in refreshed]
            cur.execute("DELETE FROM song_artist WHERE song_key = ANY(%s)", (refreshed_keys,))
            cur.executemany(
                "INSERT INTO song_artist (song_key, artist_key, artist_order) VALUES (%s, %s, %s)",
                [(song_key[r.uri], artist_key[normalize_artist(name)], order)
                 for r in refreshed for order, name in enumerate(r.artists, start=1)],
            )

        # 5. Hechos de la semana.
        cur.executemany(
            """
            INSERT INTO fact_chart_week (week_key, song_key, rank, peak_rank, previous_rank,
                                         weeks_on_chart, streams)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [(wk, song_key[r.uri], r.rank, r.peak_rank, r.previous_rank, r.weeks_on_chart, r.streams)
             for r in rows],
        )

        # 5b. Si las semanas llegaron en desorden, una canción pudo quedar
        #     partida en dos song_key; se unen aquí, en la misma transacción.
        merged = reconcile(cur, log)

        # 6. Registro del archivo, con los lugares que no traía el CSV.
        missing = missing_ranks(rows)
        notes = []
        if previous:
            notes.append(f"reemplaza a {previous[0]}")
        if missing:
            notes.append(f"el CSV no trae los lugares {missing}")
        cur.execute(
            """
            INSERT INTO etl_loaded_file (file_sha256, file_name, chart_week_end, row_count,
                                         raw_path, status, detail)
            VALUES (%s, %s, %s, %s, %s, 'loaded', %s)
            """,
            (sha256, file_name, week_end, len(rows), raw_path, "; ".join(notes) or None),
        )

    return {
        "replaced": previous[0] if previous else None,
        "missing": missing,
        "new_songs": len(new_songs),
        "relinked": len(relinked),
        "merged": merged,
        "new_artists": len(new_artists),
        "debuts": sum(1 for r in rows if r.previous_rank is None and r.weeks_on_chart == 1),
    }


@dataclass
class Candidate:
    path: Path
    content: bytes
    sha256: str
    mtime: float
    week_end: date | None


def run(log, pg: dict) -> int:
    """Una corrida completa. Regresa el número de errores."""
    started = time.monotonic()
    stats = Counter()
    files = sorted(CHARTS_LANDING.glob("*.csv")) if CHARTS_LANDING.is_dir() else []
    log.info("inicio; carpeta=%s archivos=%d", CHARTS_LANDING, len(files))
    try:
        if not CHARTS_LANDING.is_dir():
            log.warning("No existe la carpeta de entrada %s; deja ahí los CSV del chart",
                        CHARTS_LANDING)
            return 0

        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            known = {r[0] for r in conn.execute("SELECT file_sha256 FROM etl_loaded_file")}

            candidates: list[Candidate] = []
            for path in files:
                content = path.read_bytes()
                sha256 = hashlib.sha256(content).hexdigest()
                if sha256 in known:
                    stats["omitidos"] += 1
                    continue
                known.add(sha256)  # el mismo contenido con otro nombre, en esta misma corrida
                try:
                    week_end = parse_week(path.name)
                except InvalidFile:
                    week_end = None
                candidates.append(Candidate(path, content, sha256, path.stat().st_mtime, week_end))

            # De la semana más vieja a la más nueva; en la misma semana, el
            # archivo descargado al final queda como el vigente.
            candidates.sort(key=lambda c: (c.week_end or date.min, c.mtime, c.path.name))

            for c in candidates:
                file_started = time.monotonic()
                try:
                    week_end = parse_week(c.path.name)
                    rows = parse_chart(c.content)
                except InvalidFile as e:
                    record_rejected(conn, c.path.name, c.sha256, c.week_end, str(e))
                    stats["rechazados"] += 1
                    log.error("%s rechazado: %s. No se carga; queda registrado en "
                              "etl_loaded_file y no se vuelve a intentar", c.path.name, e)
                    continue

                try:
                    raw_path = write_raw(c.content, week_end, c.sha256)
                    result = load_file(conn, c.path.name, c.sha256, week_end, rows, raw_path, log)
                except psycopg.OperationalError:
                    raise
                except (psycopg.Error, OSError, ValueError) as e:
                    stats["errores"] += 1
                    log.error("%s no se cargó (ROLLBACK); se reintenta en la siguiente corrida: %s",
                              c.path.name, e)
                    continue

                stats["cargados"] += 1
                stats["filas"] += len(rows)
                if result["missing"]:
                    stats["incompletos"] += 1
                    log.warning("%s no trae los lugares %s (Spotify retiró esas canciones del "
                                "chart); se carga con %d filas", c.path.name, result["missing"],
                                len(rows))
                log.info(
                    "%s cargado; semana=%s..%s filas=%d canciones_nuevas=%d artistas_nuevos=%d "
                    "uri_cambiadas=%d canciones_unidas=%d debuts=%d reemplaza=%s crudo=%s "
                    "duracion=%.2fs",
                    c.path.name, week_end - timedelta(days=6), week_end, len(rows),
                    result["new_songs"], result["new_artists"], result["relinked"],
                    result["merged"], result["debuts"], result["replaced"] or "-", raw_path,
                    time.monotonic() - file_started,
                )
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; se reintenta en la siguiente corrida")
    finally:
        log.info("fin; duracion=%.2fs archivos=%d cargados=%d incompletos=%d omitidos=%d "
                 "rechazados=%d filas=%d errores=%d",
                 time.monotonic() - started, len(files), stats["cargados"], stats["incompletos"],
                 stats["omitidos"], stats["rechazados"], stats["filas"], stats["errores"])
    return stats["errores"]


def main() -> None:
    log = setup_logging(JOB)
    try:
        pg = pg_params()
    except KeyError as e:
        log.error("Falta la variable de entorno %s: revisa .env y docker-compose.yml", e)
        sys.exit(1)
    with single_run(JOB) as acquired:
        if not acquired:
            log.warning("Otra corrida de %s sigue en curso; esta se omite", JOB)
            return
        errors = run(log, pg)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
