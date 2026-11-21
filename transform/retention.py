"""retention: borra el texto de los comentarios antes de que cumpla 30 días.

Las políticas de YouTube no permiten guardar el texto de los comentarios
más de 30 días sin refrescarlo. Corre cada hora con cron y:

  1. Borra los archivos crudos data/raw/comments/AAAA-MM-DD.log cuyo día
     de llegada es de hace 29 días o más. Como el archivo junta un día
     completo, así ningún comentario del archivo llega a los 30 días.
  2. Borra de fact_comment, por lotes de 5,000 (una transacción cada uno),
     las filas con ingested_at de hace más de 29 días. Para entonces su
     semana ya quedó agregada en song_week_reaction, que se conserva.

Las estadísticas de los videos (fact_video_daily) no se tocan: no son
texto de comentarios y se pueden guardar más tiempo. Kafka conserva los
mensajes 7 días, menos que el límite.
"""

import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from common.jobs import RAW_DIR, pg_params, setup_logging, single_run

JOB = "retention"
COMMENTS_RAW = RAW_DIR / "comments"
MEXICO = ZoneInfo("America/Mexico_City")
KEEP_DAYS = 29
BATCH = 5_000
FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")


def run(log, pg: dict) -> int:
    started = time.monotonic()
    cutoff_day = datetime.now(MEXICO).date() - timedelta(days=KEEP_DAYS)
    files_deleted, rows_deleted, errors = 0, 0, 0
    log.info("inicio; se borra lo anterior a %s", cutoff_day)
    try:
        if COMMENTS_RAW.is_dir():
            for path in sorted(COMMENTS_RAW.glob("*.log")):
                match = FILE_RE.match(path.name)
                if match and datetime.fromisoformat(match.group(1)).date() <= cutoff_day:
                    path.unlink()
                    files_deleted += 1
                    log.info("borrado %s", path.name)

        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            while True:
                with conn.transaction():
                    deleted = conn.execute(
                        """
                        DELETE FROM fact_comment
                        WHERE comment_id IN (
                            SELECT comment_id FROM fact_comment
                            WHERE ingested_at < now() - make_interval(days => %s)
                            LIMIT %s)
                        """,
                        (KEEP_DAYS, BATCH),
                    ).rowcount
                rows_deleted += deleted
                if deleted < BATCH:
                    break
    except psycopg.OperationalError as e:
        errors += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente hora: %s", e)
    except Exception:
        errors += 1
        log.exception("Error inesperado; se reintenta en la siguiente hora")
    finally:
        log.info("fin; duracion=%.1fs archivos_borrados=%d filas_borradas=%d errores=%d",
                 time.monotonic() - started, files_deleted, rows_deleted, errors)
    return errors


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
