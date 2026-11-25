"""comments_producer: pide los comentarios nuevos de cada video y los publica en Kafka.

Es un servicio que se repite cada hora (RFC, sección 7 d). En cada ciclo:

  1. Elige los videos que toca leer: los del top 50 de la semana más
     reciente, cada hora; los demás videos seguidos, una vez al día. Se
     saltan los que tienen los comentarios desactivados o ya no existen,
     y los de canciones con la letra en inglés: quedan fuera del análisis
     (el modelo de sentimiento es para español y sus videos juntan
     comentarios de todo el mundo).
  2. Comentarios nuevos: pide commentThreads.list ordenado por fecha, del
     más nuevo al más viejo, hasta llegar a la marca de agua del video
     (video_watermark.last_published_at).
  3. Carga histórica: la primera vez, sigue página por página hacia atrás
     hasta el inicio de la historia del chart (el viernes de la semana
     más vieja cargada). Guarda la página siguiente después de cada una,
     así que si se acaba la cuota sigue al otro día donde se quedó. Se
     detiene cuando la cuota del día llega a BACKFILL_BUDGET (7,500), para
     no quitarle cuota a la lectura de cada hora.
  4. Cada comentario se publica en el tópico youtube_comments con el
     videoId como llave. El mensaje se arma con una lista blanca de
     campos: NUNCA lleva el nombre, canal, foto ni URL del autor.
  5. La marca de agua avanza solo después de kafka.flush() sin errores:
     entrega al menos una vez. Si algo falla, la siguiente lectura repite
     esa página y los duplicados se eliminan después por comment_id.

No escribe el texto de los comentarios en su log (política de retención
de 30 días de YouTube): solo conteos.
"""

import json
import os
import signal
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import psycopg
from confluent_kafka import Producer

from common.jobs import pg_params, setup_logging
from ingestion.youtube_client import (DAILY_QUOTA, ApiKeyError, CommentsDisabled, InvalidPageToken,
                                      NetworkError, QuotaExceeded, VideoNotFound, YouTubeClient,
                                      YouTubeError)

JOB = "comments_producer"
CYCLE_SECONDS = 3600
DAILY_HOURS = 24
BACKFILL_BUDGET = 7_500
TOP_N = 50
PAGE_SIZE = 100
FLUSH_TIMEOUT = 30
MAX_NETWORK_FAILURES = 3     # videos seguidos sin red: se corta el ciclo en lugar de seguir fallando

VIDEOS_SQL = """
    WITH latest AS (SELECT max(week_key) AS week_key FROM fact_chart_week)
    SELECT s.youtube_video_id,
           min(f.rank) FILTER (WHERE f.week_key = (SELECT week_key FROM latest)) AS current_rank,
           w.last_published_at, w.backfill_page_token, COALESCE(w.backfill_done, false),
           w.last_polled_at, w.disabled_reason
    FROM dim_song s
    LEFT JOIN fact_chart_week f ON f.song_key = s.song_key
    LEFT JOIN video_watermark w ON w.video_id = s.youtube_video_id
    WHERE s.youtube_video_id IS NOT NULL
      AND s.lyrics_language IS DISTINCT FROM 'en'   -- fuera del análisis, igual que sus letras
    GROUP BY s.youtube_video_id, w.video_id
    ORDER BY current_rank NULLS LAST, s.youtube_video_id
"""

SAVE_SQL = """
    INSERT INTO video_watermark (video_id, last_published_at, backfill_page_token, backfill_done,
                                 last_polled_at, disabled_reason, updated_at)
    VALUES (%(video_id)s, %(last_published_at)s, %(backfill_page_token)s, %(backfill_done)s,
            %(last_polled_at)s, %(disabled_reason)s, now())
    ON CONFLICT (video_id) DO UPDATE
    SET last_published_at = EXCLUDED.last_published_at,
        backfill_page_token = EXCLUDED.backfill_page_token,
        backfill_done = EXCLUDED.backfill_done, last_polled_at = EXCLUDED.last_polled_at,
        disabled_reason = EXCLUDED.disabled_reason, updated_at = now()
"""

stop_requested = False


class DeliveryError(Exception):
    """Kafka no confirmó todos los mensajes."""


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def comment_message(thread: dict, fetched_at: str, source: str) -> dict:
    """Mensaje para Kafka con una lista blanca de campos. Nada del autor."""
    snippet = thread["snippet"]
    top = snippet["topLevelComment"]["snippet"]
    return {
        "comment_id": thread["id"],
        "video_id": snippet.get("videoId") or top.get("videoId"),
        "text_original": top.get("textOriginal", ""),
        "like_count": top.get("likeCount", 0),
        "reply_count": snippet.get("totalReplyCount", 0),
        "published_at": top["publishedAt"],
        "updated_at": top.get("updatedAt"),
        "fetched_at": fetched_at,
        "source": source,       # poll (comentario nuevo) o backfill (carga histórica)
    }


class KafkaSink:
    def __init__(self, bootstrap: str, topic: str):
        self.topic = topic
        self.failed = 0
        self.producer = Producer({
            "bootstrap.servers": bootstrap,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 50,
        })

    def _on_delivery(self, err, msg) -> None:
        if err is not None:
            self.failed += 1

    def send(self, message: dict) -> None:
        self.producer.produce(self.topic, key=message["video_id"].encode(),
                              value=json.dumps(message, ensure_ascii=False).encode("utf-8"),
                              on_delivery=self._on_delivery)
        self.producer.poll(0)

    def flush(self) -> None:
        """Espera las confirmaciones. Lanza DeliveryError si faltó alguna."""
        remaining = self.producer.flush(FLUSH_TIMEOUT)
        failed, self.failed = self.failed, 0
        if remaining or failed:
            raise DeliveryError(f"{remaining} mensajes sin confirmar y {failed} con error")


def fetch_page(yt: YouTubeClient, video_id: str, page_token: str | None) -> dict:
    return yt.call("commentThreads.list", part="snippet", videoId=video_id, order="time",
                   maxResults=PAGE_SIZE, textFormat="plainText", pageToken=page_token)


def poll_video(yt: YouTubeClient, sink: KafkaSink, conn: psycopg.Connection, state: dict,
               since: datetime, stats: Counter) -> None:
    """Lee un video: primero los comentarios nuevos y luego, si falta, la carga histórica."""
    video_id = state["video_id"]

    def save() -> None:
        with conn.transaction():
            conn.execute(SAVE_SQL, state)

    # 1. Comentarios nuevos, hasta la marca de agua. Un video sin
    #    comentarios termina su carga histórica sin marca: se usa el inicio
    #    de la historia como marca.
    if state["last_published_at"] is not None or state["backfill_done"]:
        mark = state["last_published_at"] or since
        newest, token, sent = mark, None, 0
        while True:
            response = fetch_page(yt, video_id, token)
            stats["paginas"] += 1
            fetched_at = datetime.now(timezone.utc).isoformat()
            reached = False
            for thread in response.get("items", []):
                message = comment_message(thread, fetched_at, "poll")
                published = parse_time(message["published_at"])
                if published <= mark:
                    reached = True
                    break
                sink.send(message)
                sent += 1
                newest = max(newest, published)
            token = response.get("nextPageToken")
            if reached or not token:
                break
        sink.flush()
        state["last_published_at"] = newest
        state["last_polled_at"] = datetime.now(timezone.utc)
        save()
        stats["comentarios_nuevos"] += sent

    # 2. Carga histórica, página por página, mientras alcance la cuota. La
    #    primera página de un video nuevo se lee aunque la carga histórica ya
    #    no tenga cuota: deja puesta la marca de agua y el video entra a la
    #    lectura de cada hora desde ya.
    if not state["backfill_done"]:
        token = state["backfill_page_token"]
        token_resets = 0
        while (state["last_published_at"] is None or yt.used_today() < BACKFILL_BUDGET) \
                and not stop_requested:
            try:
                response = fetch_page(yt, video_id, token)
            except InvalidPageToken:
                if token_resets:
                    raise
                token_resets += 1
                token = None       # la página guardada caducó: se empieza de nuevo (duplicados se filtran)
                continue
            stats["paginas"] += 1
            fetched_at = datetime.now(timezone.utc).isoformat()
            done, sent = False, 0
            for thread in response.get("items", []):
                message = comment_message(thread, fetched_at, "backfill")
                published = parse_time(message["published_at"])
                if published < since:
                    done = True
                    break
                sink.send(message)
                sent += 1
                if state["last_published_at"] is None or published > state["last_published_at"]:
                    state["last_published_at"] = published
            token = response.get("nextPageToken")
            done = done or not token
            sink.flush()
            state["backfill_page_token"] = None if done else token
            state["backfill_done"] = done
            state["last_polled_at"] = datetime.now(timezone.utc)
            save()
            stats["comentarios_historicos"] += sent
            if done:
                stats["cargas_historicas_terminadas"] += 1
                break
        else:
            if not state["backfill_done"]:
                stats["cargas_historicas_pendientes"] += 1


def backfill_since(conn: psycopg.Connection) -> datetime:
    """Inicio de la historia del chart: el viernes de la semana más vieja cargada."""
    row = conn.execute(
        "SELECT min(d.date) FROM fact_chart_week f JOIN dim_date d ON d.date_key = f.week_key"
    ).fetchone()
    oldest_week_end: date = row[0] or date.today()
    start = oldest_week_end - timedelta(days=6)
    return datetime(start.year, start.month, start.day, tzinfo=timezone.utc)


def run_cycle(log, pg: dict, sink: KafkaSink) -> None:
    started = time.monotonic()
    stats = Counter()
    yt = None
    due = []
    try:
        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            now = datetime.now(timezone.utc)
            for (video_id, current_rank, last_published_at, page_token, backfill_done,
                 last_polled_at, disabled_reason) in conn.execute(VIDEOS_SQL):
                if disabled_reason:
                    continue
                hot = current_rank is not None and current_rank <= TOP_N
                daily_due = last_polled_at is None or now - last_polled_at >= timedelta(hours=DAILY_HOURS)
                if hot or daily_due or not backfill_done:
                    due.append({"video_id": video_id, "last_published_at": last_published_at,
                                "backfill_page_token": page_token, "backfill_done": backfill_done,
                                "last_polled_at": last_polled_at, "disabled_reason": None,
                                "hot": hot})
            since = backfill_since(conn)
            log.info("inicio; videos=%d (top50=%d) historia_desde=%s", len(due),
                     sum(1 for v in due if v["hot"]), since.date())
            if not due:
                return
            yt = YouTubeClient(pg)
            network_failures = 0
            for state in due:
                if stop_requested:
                    break
                state.pop("hot")
                try:
                    poll_video(yt, sink, conn, state, since, stats)
                    stats["videos"] += 1
                    network_failures = 0
                except (CommentsDisabled, VideoNotFound) as e:
                    state["disabled_reason"] = ("comments_disabled" if isinstance(e, CommentsDisabled)
                                                else "video_not_found")
                    state["last_polled_at"] = datetime.now(timezone.utc)
                    with conn.transaction():
                        conn.execute(SAVE_SQL, state)
                    stats["desactivados"] += 1
                    log.warning("video %s: %s; se deja de leer", state["video_id"],
                                state["disabled_reason"])
                except (QuotaExceeded, ApiKeyError) as e:
                    stats["errores"] += 1
                    log.error("%s; los videos que faltan se leen en el siguiente ciclo", e)
                    break
                except NetworkError as e:
                    stats["errores"] += 1
                    network_failures += 1
                    log.error("video %s no se leyó completo (la marca de agua no avanza): %s",
                              state["video_id"], e)
                    if network_failures >= MAX_NETWORK_FAILURES:
                        log.error("Sin conexión a la API en %d videos seguidos (¿sin internet?); "
                                  "los que faltan se leen en el siguiente ciclo", network_failures)
                        break
                except (YouTubeError, DeliveryError) as e:
                    stats["errores"] += 1
                    log.error("video %s no se leyó completo (la marca de agua no avanza): %s",
                              state["video_id"], e)
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en el siguiente ciclo: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; se reintenta en el siguiente ciclo")
    finally:
        units = yt.units_spent if yt else 0
        day = yt.used_today() if yt else None
        if yt:
            yt.close()
        log.info("fin; duracion=%.0fs videos=%d paginas=%d comentarios_nuevos=%d "
                 "comentarios_historicos=%d historicas_terminadas=%d historicas_pendientes=%d "
                 "desactivados=%d unidades=%d cuota_dia=%s/%d errores=%d",
                 time.monotonic() - started, stats["videos"], stats["paginas"],
                 stats["comentarios_nuevos"], stats["comentarios_historicos"],
                 stats["cargas_historicas_terminadas"], stats["cargas_historicas_pendientes"],
                 stats["desactivados"], units, day if day is not None else "-", DAILY_QUOTA,
                 stats["errores"])


def main() -> None:
    log = setup_logging(JOB)
    try:
        pg = pg_params()
        os.environ["YOUTUBE_API_KEY"]
        sink = KafkaSink(os.environ["KAFKA_BOOTSTRAP_SERVERS"], os.environ["KAFKA_TOPIC"])
    except KeyError as e:
        log.error("Falta la variable de entorno %s: revisa .env y docker-compose.yml", e)
        sys.exit(1)

    def request_stop(signum, frame) -> None:
        global stop_requested
        stop_requested = True
        log.info("señal %s recibida; termino después del video en curso", signum)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log.info("servicio iniciado; un ciclo cada %d minutos", CYCLE_SECONDS // 60)
    while not stop_requested:
        # Se usa el reloj de pared, no time.monotonic(): si la Mac se duerme,
        # al despertar el ciclo que tocaba corre de inmediato.
        next_cycle = time.time() + CYCLE_SECONDS
        run_cycle(log, pg, sink)
        while not stop_requested and time.time() < next_cycle:
            time.sleep(5)
    try:
        sink.flush()
    except DeliveryError as e:
        log.error("al terminar: %s", e)
    log.info("servicio detenido")


if __name__ == "__main__":
    main()
