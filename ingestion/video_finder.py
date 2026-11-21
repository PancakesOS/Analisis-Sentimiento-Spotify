"""video_finder: busca el video oficial en YouTube de las canciones del top 50.

Corre cada hora con cron; si no hay canciones pendientes, no gasta cuota.

  1. Pendientes: canciones que estuvieron en el top 50 en alguna de las
     últimas 12 semanas cargadas, sin youtube_video_id y que no se han
     buscado en los últimos 30 días (video_checked_at). Primero las del
     top 50 vigente, por su lugar; después las demás, por su mejor lugar.
  2. Por cada una, hace search.list (100 unidades) con el artista
     principal y el título, guarda la respuesta en raw/search/ y elige el
     video con transform/match.py.
  3. Guarda youtube_video_id, video_match (auto o review) y
     video_checked_at. Una canción, una transacción: si algo falla a la
     mitad, las que ya se guardaron no se repiten.
  4. Se detiene antes de que la cuota del día pase de SEARCH_BUDGET (7,500
     unidades), para dejar cuota al poller de comentarios y a la foto de
     estadísticas. Lo que falte se busca al día siguiente.
"""

import json
import os
import sys
import time
from collections import Counter

import psycopg

from common.jobs import RAW_DIR, pg_params, setup_logging, single_run
from ingestion.youtube_client import (COSTS, DAILY_QUOTA, ApiKeyError, QuotaExceeded, YouTubeClient,
                                      YouTubeError)
from transform.match import choose_video
from transform.normalize import search_title

JOB = "video_finder"
SEARCH_RAW = RAW_DIR / "search"
SEARCH_BUDGET = 7_500   # tope de cuota del día para búsquedas (de 9,000)
HISTORY_WEEKS = 12      # carga histórica: el top 50 de las últimas 12 semanas
TOP_N = 50
RECHECK_DAYS = 30
MAX_RESULTS = 10

PENDING_SQL = """
    WITH weeks AS (
        SELECT DISTINCT week_key FROM fact_chart_week ORDER BY week_key DESC LIMIT %(weeks)s
    ), latest AS (
        SELECT max(week_key) AS week_key FROM fact_chart_week
    )
    SELECT s.song_key, s.spotify_uri, s.track_name, a.artist_name, s.label_norm, s.label,
           min(f.rank) FILTER (WHERE f.week_key = (SELECT week_key FROM latest)) AS current_rank,
           min(f.rank) AS best_rank
    FROM fact_chart_week f
    JOIN weeks w ON w.week_key = f.week_key
    JOIN dim_song s ON s.song_key = f.song_key
    JOIN dim_artist a ON a.artist_key = s.main_artist_key
    WHERE f.rank <= %(top)s
      AND s.youtube_video_id IS NULL
      AND (s.video_checked_at IS NULL OR s.video_checked_at < now() - make_interval(days => %(recheck)s))
    GROUP BY s.song_key, s.spotify_uri, s.track_name, a.artist_name, s.label_norm, s.label
    ORDER BY current_rank NULLS LAST, best_rank, s.song_key
"""


def raw_path(spotify_uri: str):
    return SEARCH_RAW / f"{spotify_uri.rsplit(':', 1)[-1]}.json"


def rechoose_review(conn: psycopg.Connection, log) -> int:
    """Vuelve a aplicar la regla a las canciones marcadas review, con su búsqueda guardada.

    No gasta cuota. Sirve cuando la regla mejora: las elecciones viejas se
    actualizan solas. Nunca toca las corregidas a mano (manual).
    """
    changed = 0
    for song_key, uri, track_name, label_norm, label, video_id in conn.execute(
            "SELECT song_key, spotify_uri, track_name, label_norm, label, youtube_video_id "
            "FROM dim_song WHERE video_match = 'review'").fetchall():
        path = raw_path(uri)
        if not path.exists():
            continue
        items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
        choice = choose_video(items, track_name, song_artists(conn, song_key), label_norm, label)
        if choice and choice.match == "auto":
            with conn.transaction():
                conn.execute("UPDATE dim_song SET youtube_video_id = %s, video_match = 'auto', "
                             "updated_at = now() WHERE song_key = %s AND video_match = 'review'",
                             (choice.video_id, song_key))
            changed += 1
            log.info("%s: con la regla actual queda video=%s canal=«%s» regla=%s (antes %s, review)",
                     track_name, choice.video_id, choice.channel_title, choice.rule, video_id)
    return changed


def song_artists(conn: psycopg.Connection, song_key: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT a.artist_name FROM song_artist sa JOIN dim_artist a USING (artist_key) "
        "WHERE sa.song_key = %s ORDER BY sa.artist_order", (song_key,))]


def save_raw(spotify_uri: str, response: dict) -> None:
    SEARCH_RAW.mkdir(parents=True, exist_ok=True)
    target = raw_path(spotify_uri)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def run(log, pg: dict, max_songs: int | None) -> int:
    started = time.monotonic()
    stats = Counter()
    pending = []
    yt = None
    try:
        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            pending = conn.execute(PENDING_SQL, {"weeks": HISTORY_WEEKS, "top": TOP_N,
                                                 "recheck": RECHECK_DAYS}).fetchall()
            if max_songs is not None:
                pending = pending[:max_songs]
            log.info("inicio; pendientes=%d", len(pending))
            stats["reelegidas"] = rechoose_review(conn, log)
            if not pending:
                return 0
            yt = YouTubeClient(pg)
            for (song_key, uri, track_name, artist, label_norm, label, current_rank,
                 best_rank) in pending:
                used = yt.used_today()
                if used + COSTS["search.list"] > SEARCH_BUDGET:
                    log.warning("cuota del día en %d/%d; las búsquedas paran en %d para dejar "
                                "cuota al poller; %d canciones quedan para mañana",
                                used, DAILY_QUOTA, SEARCH_BUDGET, len(pending) - stats["buscadas"])
                    break
                query = f"{artist} {search_title(track_name)}"
                try:
                    response = yt.call("search.list", part="snippet", q=query, type="video",
                                       maxResults=MAX_RESULTS, regionCode="MX")
                except QuotaExceeded as e:
                    log.error("%s; %d canciones quedan para mañana", e,
                              len(pending) - stats["buscadas"])
                    break
                except ApiKeyError as e:
                    stats["errores"] += 1
                    log.error("%s; no se busca nada más en esta corrida", e)
                    break
                except YouTubeError as e:
                    stats["errores"] += 1
                    log.error("búsqueda fallida para %s (%s): %s", track_name, uri, e)
                    continue
                stats["buscadas"] += 1
                save_raw(uri, response)
                choice = choose_video(response.get("items", []), track_name,
                                      song_artists(conn, song_key), label_norm, label)
                with conn.transaction():
                    conn.execute(
                        "UPDATE dim_song SET youtube_video_id = %s, video_match = %s, "
                        "video_checked_at = now(), updated_at = now() WHERE song_key = %s",
                        (choice.video_id if choice else None, choice.match if choice else None,
                         song_key),
                    )
                if choice is None:
                    stats["sin_resultados"] += 1
                    log.warning("sin resultados para «%s» de %s", query, track_name)
                    continue
                stats[choice.match] += 1
                log.info("%s — %s (lugar actual %s, mejor %s): video=%s canal=«%s» regla=%s "
                         "match=%s", artist, track_name, current_rank or "-", best_rank,
                         choice.video_id, choice.channel_title, choice.rule, choice.match)
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; se reintenta en la siguiente corrida")
    finally:
        units = yt.units_spent if yt else 0
        day = yt.used_today() if yt else None
        if yt:
            yt.close()
        log.info("fin; duracion=%.0fs pendientes=%d buscadas=%d auto=%d review=%d "
                 "sin_resultados=%d reelegidas=%d unidades=%d cuota_dia=%s/%d errores=%d",
                 time.monotonic() - started, len(pending), stats["buscadas"], stats["auto"],
                 stats["review"], stats["sin_resultados"], stats["reelegidas"], units,
                 day if day is not None else "-", DAILY_QUOTA, stats["errores"])
    return stats["errores"]


def main() -> None:
    log = setup_logging(JOB)
    try:
        pg = pg_params()
        os.environ["YOUTUBE_API_KEY"]
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
