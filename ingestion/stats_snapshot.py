"""stats_snapshot: foto de vistas, likes y comentarios de cada video seguido.

Es el único dato que no se puede recuperar después: la API solo da el
total de hoy, no el de un día que ya pasó. Por eso corre CADA HORA (a los
50 minutos) y no solo a las 23:50 como decía el RFC: cada corrida
sobrescribe la foto del día (llave date_key + song_key), así que queda la
última que se tomó. Si la Mac está dormida a las 23:50, el día no se
pierde: queda la foto de la última hora en que estuvo despierta. Cuesta
1 unidad por cada 50 videos, unas 2 a 4 unidades por corrida.

  1. Toma los youtube_video_id de dim_song (puede haber dos canciones con
     el mismo video).
  2. Hace videos.list en lotes de 50 y guarda la respuesta en
     raw/stats/AAAA-MM-DD/HHMM.json.
  3. Upsert en fact_video_daily, un lote por transacción.
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

from common.jobs import RAW_DIR, pg_params, setup_logging, single_run
from ingestion.youtube_client import (DAILY_QUOTA, ApiKeyError, QuotaExceeded, YouTubeClient,
                                      YouTubeError)

JOB = "stats_snapshot"
STATS_RAW = RAW_DIR / "stats"
MEXICO = ZoneInfo("America/Mexico_City")
BATCH = 50

UPSERT_SQL = """
    INSERT INTO fact_video_daily (date_key, song_key, video_id, view_count, like_count,
                                  comment_count, captured_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (date_key, song_key) DO UPDATE
    SET video_id = EXCLUDED.video_id, view_count = EXCLUDED.view_count,
        like_count = EXCLUDED.like_count, comment_count = EXCLUDED.comment_count,
        captured_at = EXCLUDED.captured_at
"""


def to_int(value) -> int | None:
    return int(value) if value is not None else None


def run(log, pg: dict) -> int:
    started = time.monotonic()
    now = datetime.now(MEXICO)
    date_key = int(now.strftime("%Y%m%d"))
    stats = Counter()
    yt = None
    try:
        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            songs_by_video = defaultdict(list)
            for song_key, video_id in conn.execute(
                    "SELECT song_key, youtube_video_id FROM dim_song "
                    "WHERE youtube_video_id IS NOT NULL ORDER BY youtube_video_id, song_key"):
                songs_by_video[video_id].append(song_key)
            videos = list(songs_by_video)
            log.info("inicio; fecha=%s videos=%d", now.date(), len(videos))
            if not videos:
                return 0
            yt = YouTubeClient(pg)
            raw_dir = STATS_RAW / now.date().isoformat()
            raw_dir.mkdir(parents=True, exist_ok=True)
            responses = []
            for i in range(0, len(videos), BATCH):
                batch = videos[i:i + BATCH]
                try:
                    response = yt.call("videos.list", part="statistics", id=",".join(batch),
                                       maxResults=BATCH)
                except (QuotaExceeded, ApiKeyError) as e:
                    stats["errores"] += 1
                    log.error("%s; la foto queda incompleta y se completa en la siguiente hora", e)
                    break
                except YouTubeError as e:
                    stats["errores"] += 1
                    log.error("videos.list falló para el lote %d: %s", i // BATCH + 1, e)
                    continue
                responses.append(response)
                found = {item["id"]: item.get("statistics", {}) for item in response.get("items", [])}
                rows = []
                for video_id in batch:
                    st = found.get(video_id)
                    if st is None or st.get("viewCount") is None:
                        stats["no_disponibles"] += 1
                        log.warning("el video %s no regresó estadísticas (borrado o privado)", video_id)
                        continue
                    for song_key in songs_by_video[video_id]:
                        rows.append((date_key, song_key, video_id, int(st["viewCount"]),
                                     to_int(st.get("likeCount")), to_int(st.get("commentCount")),
                                     datetime.now(MEXICO)))
                with conn.transaction(), conn.cursor() as cur:
                    cur.executemany(UPSERT_SQL, rows)
                stats["filas"] += len(rows)
                stats["videos"] += len({row[2] for row in rows})

            target = raw_dir / f"{now:%H%M}.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(responses), encoding="utf-8")
            os.replace(tmp, target)
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente hora: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; se reintenta en la siguiente hora")
    finally:
        units = yt.units_spent if yt else 0
        day = yt.used_today() if yt else None
        if yt:
            yt.close()
        log.info("fin; duracion=%.1fs videos_con_datos=%d filas=%d no_disponibles=%d unidades=%d "
                 "cuota_dia=%s/%d errores=%d", time.monotonic() - started, stats["videos"],
                 stats["filas"], stats["no_disponibles"], units,
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
    with single_run(JOB) as acquired:
        if not acquired:
            log.warning("Otra corrida de %s sigue en curso; esta se omite", JOB)
            return
        errors = run(log, pg)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
