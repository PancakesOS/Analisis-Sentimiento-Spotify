"""lyrics_fetcher: busca la letra de cada canción del chart en LRCLIB.

Corre cada hora con cron (el RFC decía diario a las 03:00; ver la nota de
ingestion/crontab sobre la Mac dormida). Si no hay canciones pendientes,
no hace ninguna llamada. LRCLIB es gratuito, no pide key y no tiene cuota.

  1. Pendientes: canciones sin lyrics_status, o con not_found de hace más
     de RECHECK_DAYS (7) días, por si alguien subió la letra después.
     Primero las que aparecen en las semanas más recientes del chart.
  2. Busca con GET /api/search?track_name=…&artist_name=… (el título sin
     "(from …)" ni invitados y el artista principal). Si ningún resultado
     corresponde a la canción, intenta una búsqueda libre (?q=título
     artista), que tolera mejor las diferencias en el nombre del artista.
  3. Guarda las respuestas completas en raw/lyrics/<id de la URI>.json,
     escribiendo un archivo temporal y renombrándolo, así que nunca queda
     un archivo a medias.
  4. Elige la versión de consenso con transform/match.choose_lyrics y
     actualiza lyrics_status (found, not_found o instrumental), lrclib_id
     y lyrics_checked_at. Una canción, una transacción.
  5. Si la versión elegida cambió, borra el sentimiento anterior de la
     letra para que lyrics_processor la vuelva a analizar.

Si LRCLIB falla 5 veces seguidas, la corrida se detiene y lo que faltó se
intenta en la siguiente hora.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

import psycopg

from common.jobs import RAW_DIR, pg_params, setup_logging, single_run
from transform.match import choose_lyrics
from transform.normalize import search_title

JOB = "lyrics_fetcher"
LYRICS_RAW = RAW_DIR / "lyrics"
API_URL = "https://lrclib.net/api/search"
USER_AGENT = "lyrics-fetcher/1.0 (python-urllib)"
TIMEOUT = 20
RETRIES = 2
PAUSE_SECONDS = 0.2         # entre llamadas, para no saturar un servicio gratuito
MAX_CONSECUTIVE_FAILURES = 5
RECHECK_DAYS = 7

PENDING_SQL = """
    SELECT s.song_key, s.spotify_uri, s.track_name, s.lrclib_id,
           max(f.week_key) AS last_week, min(f.rank) AS best_rank
    FROM dim_song s
    LEFT JOIN fact_chart_week f ON f.song_key = s.song_key
    WHERE s.lyrics_status IS NULL
       OR (s.lyrics_status = 'not_found'
           AND s.lyrics_checked_at < now() - make_interval(days => %(recheck)s))
    GROUP BY s.song_key
    ORDER BY last_week DESC NULLS LAST, best_rank, s.song_key
"""

ARTISTS_SQL = """
    SELECT a.artist_name FROM song_artist sa JOIN dim_artist a USING (artist_key)
    WHERE sa.song_key = %s ORDER BY sa.artist_order
"""

# Si la versión elegida cambia, el sentimiento anterior ya no corresponde.
SAVE_SQL = """
    UPDATE dim_song SET
        lyrics_status = %(status)s,
        lrclib_id = %(lrclib_id)s,
        lyrics_checked_at = now(),
        lyrics_language = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                               THEN NULL ELSE lyrics_language END,
        lyrics_sentiment = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                                THEN NULL ELSE lyrics_sentiment END,
        lyrics_p_pos = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                            THEN NULL ELSE lyrics_p_pos END,
        lyrics_p_neu = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                            THEN NULL ELSE lyrics_p_neu END,
        lyrics_p_neg = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                            THEN NULL ELSE lyrics_p_neg END,
        lyrics_emotion = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                              THEN NULL ELSE lyrics_emotion END,
        model_version = CASE WHEN lrclib_id IS DISTINCT FROM %(lrclib_id)s
                             THEN NULL ELSE model_version END,
        updated_at = now()
    WHERE song_key = %(song_key)s
"""


class LrclibError(Exception):
    """LRCLIB no respondió bien después de los reintentos."""


def lrclib_search(params: dict) -> list[dict]:
    """GET /api/search con reintentos. Regresa la lista de resultados."""
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = json.load(response)
            if not isinstance(data, list):
                raise LrclibError(f"respuesta inesperada (no es una lista): {str(data)[:120]}")
            return data
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
            if e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = str(e)
        time.sleep(2 * (attempt + 1))
    raise LrclibError(last_error or "error desconocido")


def raw_path(spotify_uri: str):
    return LYRICS_RAW / f"{spotify_uri.rsplit(':', 1)[-1]}.json"


def save_raw(spotify_uri: str, payload: dict) -> None:
    LYRICS_RAW.mkdir(parents=True, exist_ok=True)
    target = raw_path(spotify_uri)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def fetch_song(track_name: str, artists: list[str]):
    """Hace la búsqueda (y la libre si hace falta). Regresa (consultas, elección)."""
    title = search_title(track_name)
    attempts = [{"track_name": title, "artist_name": artists[0]},
                {"q": f"{title} {artists[0]}"}]
    queries, choice = [], None
    for i, params in enumerate(attempts):
        if i:
            time.sleep(PAUSE_SECONDS)
        results = lrclib_search(params)
        queries.append({"params": params, "results": results})
        choice = choose_lyrics(results, track_name, artists)
        if choice is not None:
            break
    return queries, choice


def run(log, pg: dict, max_songs: int | None) -> int:
    started = time.monotonic()
    stats = Counter()
    pending = []
    try:
        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            pending = conn.execute(PENDING_SQL, {"recheck": RECHECK_DAYS}).fetchall()
            if max_songs is not None:
                pending = pending[:max_songs]
            log.info("inicio; pendientes=%d", len(pending))
            failures = 0
            for song_key, uri, track_name, old_id, _, _ in pending:
                artists = [r[0] for r in conn.execute(ARTISTS_SQL, (song_key,))]
                if not artists:
                    continue
                try:
                    queries, choice = fetch_song(track_name, artists)
                except LrclibError as e:
                    stats["errores"] += 1
                    failures += 1
                    log.error("LRCLIB falló para «%s» de %s: %s", track_name, artists[0], e)
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        log.error("%d fallas seguidas; se detiene y sigue en la siguiente corrida",
                                  failures)
                        break
                    continue
                failures = 0
                save_raw(uri, {"spotify_uri": uri, "track_name": track_name, "artists": artists,
                               "fetched_at": datetime.now(timezone.utc).isoformat(),
                               "queries": queries})
                status = choice.status if choice else "not_found"
                lrclib_id = choice.lrclib_id if choice else None
                with conn.transaction():
                    conn.execute(SAVE_SQL, {"status": status, "lrclib_id": lrclib_id,
                                            "song_key": song_key})
                stats[status] += 1
                if len(queries) > 1:
                    stats["busqueda_libre"] += 1
                if old_id is not None and old_id != lrclib_id:
                    stats["version_cambiada"] += 1
                if status == "not_found":
                    log.info("sin letra: «%s» de %s", track_name, artists[0])
                time.sleep(PAUSE_SECONDS)
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; se reintenta en la siguiente corrida")
    finally:
        log.info("fin; duracion=%.0fs pendientes=%d encontradas=%d instrumentales=%d "
                 "sin_letra=%d busqueda_libre=%d version_cambiada=%d errores=%d",
                 time.monotonic() - started, len(pending), stats["found"], stats["instrumental"],
                 stats["not_found"], stats["busqueda_libre"], stats["version_cambiada"],
                 stats["errores"])
    return stats["errores"]


def main() -> None:
    log = setup_logging(JOB)
    try:
        pg = pg_params()
    except KeyError as e:
        log.error("Falta la variable de entorno %s: revisa .env y docker-compose.yml", e)
        sys.exit(1)
    max_songs = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with single_run(JOB) as acquired:
        if not acquired:
            log.warning("Otra corrida de %s sigue en curso; esta se omite", JOB)
            return
        errors = run(log, pg, max_songs)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
