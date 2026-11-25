"""Verificación: la base cuadra con los archivos de entrada y la zona cruda.

Se ejecuta en el contenedor batch, que ve la carpeta de entrada, el crudo
y PostgreSQL:

    docker compose exec batch /app/.venv/bin/python -m scripts.verify

Revisa todo el flujo (RFC, sección 7 f): el chart, YouTube, las letras,
los comentarios, el agregado semanal y las vistas de las preguntas.

  1. Cada CSV de la carpeta de entrada está registrado en etl_loaded_file.
  2. Cada semana cargada tiene en fact_chart_week tantas filas como dice
     su archivo, con rank del 1 al 200 sin repetir (puede faltar alguno
     que Spotify retiró; chart_loader lo anota en etl_loaded_file.detail);
     no hay semanas en fact_chart_week sin un archivo vigente.
  3. La copia de cada archivo vigente existe en la zona cruda y su sha256
     coincide.
  4. Cada canción aparece en al menos una semana y su first_seen_week es
     la primera semana en que aparece.
  5. Cada canción tiene artistas en song_artist, con orden 1, 2, 3… sin
     huecos, y el de orden 1 es su main_artist_key.
     No hay artistas sin canciones.
  6. Cada URI de alta de dim_song está en song_uri apuntando a su canción,
     y no queda ninguna canción partida por un cambio de URI.
  7. YouTube: ningún día pasa del tope de 9,000 unidades de cuota; ningún
     mensaje del crudo de comentarios trae campos del autor; ningún
     archivo del crudo tiene más de 30 días. Muestra la cobertura de
     videos del top 50 vigente.
  8. Letras: cada canción con letra tiene su versión en el crudo; la
     cobertura llega a la meta del RFC (85 %) cuando ya no hay pendientes.
  9. Comentarios (conciliación crudo ↔ base): cada comentario válido del
     crudo, hasta donde llegó comments_processor y de un video que
     pertenece a una canción, está en fact_comment; fact_comment no tiene
     filas de más de 30 días de ingeridas.
 10. Agregado semanal: ninguna pareja canción–semana está pendiente de
     recalcular y, mientras sus comentarios sigan en la base, su
     n_comments coincide con fact_comment.
 11. Las vistas v_p1 a v_p7 existen y se pueden consultar.

Termina con OK o con la lista de diferencias (y código de salida 1).
"""

import hashlib
import json
import sys
import time
from datetime import date, timedelta

import psycopg

from common.jobs import LANDING_DIR, RAW_DIR, pg_params, setup_logging
from transform.aggregate_week import STALE_SQL
from transform.comments_processor import (STATE_FILE, InvalidLine, load_video_songs, new_lines,
                                          parse_comment)
from transform.song_identity import PAIRS_SQL

JOB = "verify"
CHARTS_LANDING = LANDING_DIR / "charts"


def check_landing(conn) -> list[str]:
    known = {r[0]: r[1] for r in conn.execute("SELECT file_sha256, status FROM etl_loaded_file")}
    diffs = []
    files = sorted(CHARTS_LANDING.glob("*.csv")) if CHARTS_LANDING.is_dir() else []
    for path in files:
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha256 not in known:
            diffs.append(f"{path.name}: todavía no se carga (espera la siguiente corrida de chart_loader)")
    return diffs


def check_weeks(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT COALESCE(l.chart_week_end, d.date) AS week, l.file_name, l.row_count,
               count(f.song_key) AS facts, min(f.rank), max(f.rank), count(DISTINCT f.rank)
        FROM (SELECT * FROM etl_loaded_file WHERE status = 'loaded') l
        FULL JOIN (fact_chart_week f JOIN dim_date d ON d.date_key = f.week_key)
               ON d.date = l.chart_week_end
        GROUP BY 1, 2, 3
        ORDER BY 1
        """
    ).fetchall()
    diffs = []
    for week, file_name, row_count, facts, min_rank, max_rank, distinct_ranks in rows:
        if file_name is None:
            diffs.append(f"semana {week}: {facts} filas en fact_chart_week sin archivo vigente")
        elif facts != row_count:
            diffs.append(f"semana {week}: el archivo {file_name} tiene {row_count} filas "
                         f"y fact_chart_week {facts}")
        elif distinct_ranks != facts or min_rank < 1 or max_rank > 200:
            diffs.append(f"semana {week}: rank de {min_rank} a {max_rank} con "
                         f"{distinct_ranks} valores distintos en {facts} filas")
    return diffs


def check_raw(conn) -> list[str]:
    diffs = []
    for file_name, sha256, raw_path in conn.execute(
            "SELECT file_name, file_sha256, raw_path FROM etl_loaded_file "
            "WHERE status IN ('loaded', 'replaced') ORDER BY chart_week_end"):
        path = RAW_DIR / raw_path
        if not path.exists():
            diffs.append(f"{file_name}: falta la copia cruda {raw_path}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            diffs.append(f"{file_name}: la copia cruda {raw_path} no tiene el mismo sha256")
    return diffs


def check_songs(conn) -> list[str]:
    diffs = []
    for uri, first_seen, first_fact in conn.execute(
            """
            SELECT s.spotify_uri, s.first_seen_week, min(f.week_key)
            FROM dim_song s LEFT JOIN fact_chart_week f ON f.song_key = s.song_key
            GROUP BY s.song_key
            HAVING min(f.week_key) IS DISTINCT FROM s.first_seen_week
            ORDER BY s.spotify_uri
            """):
        if first_fact is None:
            diffs.append(f"{uri}: está en dim_song pero en ninguna semana del chart")
        else:
            diffs.append(f"{uri}: first_seen_week={first_seen}, pero aparece desde {first_fact}")
    return diffs


def check_bridge(conn) -> list[str]:
    diffs = []
    for uri, n_artists, max_order, main_in_bridge in conn.execute(
            """
            SELECT s.spotify_uri, count(sa.artist_key), max(sa.artist_order),
                   bool_or(sa.artist_order = 1 AND sa.artist_key = s.main_artist_key)
            FROM dim_song s LEFT JOIN song_artist sa ON sa.song_key = s.song_key
            GROUP BY s.song_key
            HAVING count(sa.artist_key) = 0
                OR max(sa.artist_order) <> count(sa.artist_key)
                OR NOT bool_or(sa.artist_order = 1 AND sa.artist_key = s.main_artist_key)
            ORDER BY s.spotify_uri
            """):
        diffs.append(f"{uri}: {n_artists} artistas en song_artist, orden máximo {max_order}, "
                     f"artista principal en orden 1: {'sí' if main_in_bridge else 'no'}")
    for (name,) in conn.execute(
            "SELECT a.artist_name FROM dim_artist a "
            "WHERE NOT EXISTS (SELECT 1 FROM song_artist sa WHERE sa.artist_key = a.artist_key) "
            "ORDER BY a.artist_name"):
        diffs.append(f"artista sin canciones: {name}")
    return diffs


def check_uris(conn) -> list[str]:
    diffs = []
    for uri, mapped in conn.execute(
            """
            SELECT s.spotify_uri, u.song_key
            FROM dim_song s LEFT JOIN song_uri u ON u.spotify_uri = s.spotify_uri
            WHERE u.song_key IS DISTINCT FROM s.song_key OR u.link_reason <> 'original'
            ORDER BY 1
            """):
        diffs.append(f"{uri}: es la URI de alta de su canción pero song_uri la asigna a {mapped}")
    # Canciones que siguen partidas: la fila dice venir de un lugar que la
    # semana anterior ocupaba la misma canción con otra song_key.
    for from_key, into_key in conn.execute(PAIRS_SQL):
        diffs.append(f"song_key {from_key} y {into_key} son la misma canción con otra URI "
                     "y siguen separadas")
    return diffs


AUTHOR_FIELDS = {"authorDisplayName", "authorChannelId", "authorChannelUrl", "authorProfileImageUrl"}
COMMENT_FIELDS = {"comment_id", "video_id", "text_original", "like_count", "reply_count",
                  "published_at", "updated_at", "fetched_at", "source"}


def check_youtube(conn, log) -> list[str]:
    """Cuota, cobertura de videos y crudo de comentarios (privacidad y retención)."""
    diffs = []
    for day, units, calls in conn.execute(
            "SELECT quota_date, sum(units), sum(calls) FROM etl_quota_usage "
            "GROUP BY 1 ORDER BY 1 DESC LIMIT 7"):
        log.info("cuota %s (hora del Pacífico): %d unidades en %d llamadas", day, units, calls)
        if units > 9_000:
            diffs.append(f"la cuota del {day} pasó del tope de 9,000: {units}")

    top, with_video, review = conn.execute(
        """
        SELECT count(*), count(s.youtube_video_id), count(*) FILTER (WHERE s.video_match = 'review')
        FROM fact_chart_week f JOIN dim_song s USING (song_key)
        WHERE f.week_key = (SELECT max(week_key) FROM fact_chart_week) AND f.rank <= 50
        """).fetchone()
    log.info("top 50 vigente: %d de %d con video (%d marcados para revisión)", with_video, top, review)

    files = sorted((RAW_DIR / "comments").glob("*.log")) if (RAW_DIR / "comments").is_dir() else []
    lines = invalid = 0
    ids = set()
    oldest_allowed = (date.today() - timedelta(days=30)).isoformat()
    for path in files:
        if path.stem < oldest_allowed:   # el nombre es AAAA-MM-DD: el día de llegada
            diffs.append(f"crudo de comentarios {path.name} con más de 30 días sin borrar")
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                lines += 1
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                extra = set(msg) - COMMENT_FIELDS
                if extra & AUTHOR_FIELDS or any(k.lower().startswith("author") for k in msg):
                    diffs.append(f"{path.name}: un mensaje trae campos del autor ({sorted(extra)})")
                    break
                ids.add(msg.get("comment_id"))
    log.info("crudo de comentarios: %d archivos, %d líneas, %d comentarios únicos, %d líneas no JSON",
             len(files), lines, len(ids), invalid)
    return diffs


LYRICS_GOAL = 0.85


def check_lyrics(conn, log) -> list[str]:
    """Letras: versión en el crudo y cobertura contra la meta del RFC."""
    diffs = []
    songs, found, pending, analyzed, english = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE lyrics_status = 'found'),
               count(*) FILTER (WHERE lyrics_status IS NULL),
               count(*) FILTER (WHERE model_version IS NOT NULL),
               count(*) FILTER (WHERE lyrics_language = 'en')
        FROM dim_song
        """).fetchone()
    log.info("letras: %d de %d canciones con letra (%.1f %%), %d analizadas, %d en inglés, "
             "%d sin buscar", found, songs, 100 * found / songs if songs else 0, analyzed,
             english, pending)
    if songs and pending == 0 and found / songs < LYRICS_GOAL:
        diffs.append(f"cobertura de letras {found / songs:.1%}, debajo de la meta de 85 %")
    missing = 0
    for uri, lrclib_id in conn.execute(
            "SELECT spotify_uri, lrclib_id FROM dim_song WHERE lyrics_status = 'found'"):
        path = RAW_DIR / "lyrics" / f"{uri.rsplit(':', 1)[-1]}.json"
        if lrclib_id is None or not path.exists():
            missing += 1
    if missing:
        diffs.append(f"{missing} canciones con letra sin su versión en raw/lyrics/")
    return diffs


def check_comments(conn, log) -> list[str]:
    """Conciliación del crudo de comentarios contra fact_comment."""
    diffs = []
    offsets = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("offsets", {}) \
        if STATE_FILE.exists() else {}
    video_song, excluded = load_video_songs(conn)
    expected, not_processed, reasons = set(), 0, {}
    for path in sorted((RAW_DIR / "comments").glob("*.log")) if (RAW_DIR / "comments").is_dir() else []:
        upto = offsets.get(path.name, 0)
        for text, end in new_lines(path, 0):
            if end > upto:
                not_processed += 1
                continue
            try:
                c = parse_comment(text)
            except InvalidLine as e:
                reasons[e.reason] = reasons.get(e.reason, 0) + 1
                continue
            if c["video_id"] not in video_song:
                reasons["video_sin_cancion"] = reasons.get("video_sin_cancion", 0) + 1
            elif c["video_id"] in excluded:
                reasons["cancion_en_ingles"] = reasons.get("cancion_en_ingles", 0) + 1
            else:
                expected.add(c["comment_id"])
    present = {r[0] for r in conn.execute(
        "SELECT comment_id FROM fact_comment WHERE comment_id = ANY(%s)", (list(expected),))}
    total, old = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE ingested_at < now() - interval '30 days') "
        "FROM fact_comment").fetchone()
    log.info("comentarios: %d únicos válidos en el crudo procesado, %d en fact_comment (total %d), "
             "%d líneas aún sin procesar, descartes por motivo %s", len(expected), len(present),
             total, not_processed, reasons or "{}")
    if expected - present:
        diffs.append(f"{len(expected - present)} comentarios del crudo procesado no están en "
                     f"fact_comment (ej. {sorted(expected - present)[:3]})")
    if old:
        diffs.append(f"{old} filas de fact_comment con más de 30 días de ingeridas")
    return diffs


def check_reaction(conn, log) -> list[str]:
    """song_week_reaction al día y consistente con fact_comment."""
    diffs = []
    stale = conn.execute(STALE_SQL).fetchall()
    if stale:
        diffs.append(f"{len(stale)} parejas canción–semana pendientes de recalcular "
                     "(¿corrió aggregate_week después de comments_processor?)")
    mismatched = conn.execute(
        """
        SELECT count(*) FROM song_week_reaction r
        JOIN LATERAL (
            SELECT count(*) AS n FROM fact_comment f
            JOIN dim_date d ON d.date_key = f.date_key
            WHERE f.song_key = r.song_key AND NOT f.discarded
              AND to_char(d.chart_week_end, 'YYYYMMDD')::integer = r.week_key
        ) c ON true
        WHERE r.last_ingested_at > now() - interval '28 days' AND c.n <> r.n_comments
        """).fetchone()[0]
    pairs, comments = conn.execute(
        "SELECT count(*), COALESCE(sum(n_comments), 0) FROM song_week_reaction").fetchone()
    log.info("reacción semanal: %d parejas canción–semana con %d comentarios", pairs, comments)
    if mismatched:
        diffs.append(f"{mismatched} parejas con n_comments distinto al de fact_comment")
    return diffs


def check_views(conn, log) -> list[str]:
    diffs = []
    counts = []
    for i in range(1, 8):
        try:
            with conn.transaction():
                counts.append(f"v_p{i}={conn.execute(f'SELECT count(*) FROM v_p{i}').fetchone()[0]}")
        except psycopg.Error as e:
            diffs.append(f"v_p{i} no se puede consultar: {e}")
    log.info("vistas: %s", " ".join(counts))
    return diffs


def main() -> None:
    log = setup_logging(JOB)
    started = time.monotonic()
    log.info("inicio")
    try:
        pg = pg_params()
    except KeyError as e:
        log.error("Falta la variable de entorno %s: ejecuta verify en el contenedor batch", e)
        sys.exit(1)

    try:
        with psycopg.connect(**pg, connect_timeout=10) as conn:
            summary = conn.execute(
                """
                SELECT (SELECT count(*) FROM etl_loaded_file WHERE status = 'loaded'),
                       (SELECT count(*) FROM etl_loaded_file WHERE status = 'replaced'),
                       (SELECT count(*) FROM etl_loaded_file WHERE status = 'rejected'),
                       (SELECT count(*) FROM fact_chart_week),
                       (SELECT count(*) FROM dim_song),
                       (SELECT count(*) FROM dim_artist),
                       (SELECT count(*) FROM song_artist),
                       (SELECT min(chart_week_end) FROM etl_loaded_file WHERE status = 'loaded'),
                       (SELECT max(chart_week_end) FROM etl_loaded_file WHERE status = 'loaded')
                """
            ).fetchone()
            checks = [
                ("1. Archivos de entrada registrados", check_landing(conn)),
                ("2. Filas por semana = filas del archivo", check_weeks(conn)),
                ("3. Copias en la zona cruda", check_raw(conn)),
                ("4. Canciones y first_seen_week", check_songs(conn)),
                ("5. Puente canción–artista", check_bridge(conn)),
                ("6. URI de Spotify por canción", check_uris(conn)),
                ("7. YouTube: cuota y crudo de comentarios sin datos del autor", check_youtube(conn, log)),
                ("8. Letras: crudo y cobertura", check_lyrics(conn, log)),
                ("9. Comentarios: crudo = fact_comment", check_comments(conn, log)),
                ("10. Reacción por canción y semana", check_reaction(conn, log)),
                ("11. Vistas de las preguntas", check_views(conn, log)),
            ]
    except psycopg.OperationalError as e:
        log.error("No se pudo conectar a PostgreSQL: %s", e)
        sys.exit(1)

    loaded, replaced, rejected, facts, songs, artists, bridge, first_week, last_week = summary
    log.info("archivos: vigentes=%d reemplazados=%d rechazados=%d; semanas %s a %s",
             loaded, replaced, rejected, first_week, last_week)
    log.info("fact_chart_week=%d dim_song=%d dim_artist=%d song_artist=%d",
             facts, songs, artists, bridge)

    all_ok = True
    for title, diffs in checks:
        if diffs:
            all_ok = False
            log.info("%s: %d diferencias", title, len(diffs))
            for d in diffs:
                log.info("    %s", d)
        else:
            log.info("%s: coincide", title)

    log.info("fin; duracion=%.2fs resultado=%s", time.monotonic() - started,
             "OK" if all_ok else "HAY DIFERENCIAS")
    print("OK" if all_ok else "HAY DIFERENCIAS")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
