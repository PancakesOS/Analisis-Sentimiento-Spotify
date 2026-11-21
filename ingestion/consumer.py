"""consumer: lee el tópico youtube_comments y guarda cada mensaje, tal cual, en el crudo.

Viene del consumer de la Tarea 4, con dos cambios:

  * Escribe un archivo por día de llegada (hora de México):
    data/raw/comments/AAAA-MM-DD.log. Así retention.py puede borrar el
    texto de los comentarios a los 30 días borrando archivos completos.
  * No copia el contenido de los mensajes a su log, solo conteos: el log
    no se borra a los 30 días y no debe guardar texto de comentarios.

No interpreta ni filtra nada: eso le toca a comments_processor.

Entrega «al menos una vez»: el offset de un mensaje se le confirma a Kafka
solo DESPUÉS de escribirlo en el crudo. Si el consumer se cae a la mitad,
al reiniciar vuelve a leer desde el último mensaje guardado; en el peor
caso un mensaje queda dos veces en el crudo, y se descarta después por
su comment_id.
"""

import os
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from confluent_kafka import Consumer, KafkaError

from common.jobs import RAW_DIR, setup_logging

JOB = "consumer"
COMMENTS_RAW = RAW_DIR / "comments"
GROUP_ID = "ingestion"
MEXICO = ZoneInfo("America/Mexico_City")
SUMMARY_SECONDS = 600   # cada cuánto deja una línea con los conteos


def main() -> None:
    log = setup_logging(JOB)
    try:
        bootstrap_servers = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
        topic = os.environ["KAFKA_TOPIC"]
    except KeyError as e:
        log.error("Falta la variable de entorno %s: revisa docker-compose.yml", e)
        sys.exit(1)

    def on_sigterm(signum, frame) -> None:
        raise KeyboardInterrupt  # cierra el consumer y confirma los offsets guardados

    signal.signal(signal.SIGTERM, on_sigterm)

    COMMENTS_RAW.mkdir(parents=True, exist_ok=True)
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",
        "allow.auto.create.topics": True,
        # La librería confirma sola cada pocos segundos, pero solo los
        # offsets que guardamos con store_offsets(), es decir, los de
        # mensajes que ya están en el crudo.
        "enable.auto.offset.store": False,
    })
    consumer.subscribe([topic])
    log.info("inicio; suscrito a %s; el crudo se escribe en %s/AAAA-MM-DD.log", topic, COMMENTS_RAW)

    raw, raw_day = None, None
    written, empty, last_summary = 0, 0, time.monotonic()
    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if time.monotonic() - last_summary >= SUMMARY_SECONDS:
                if written or empty:
                    log.info("guardados=%d vacios=%d en los últimos %d min (archivo %s)",
                             written, empty, SUMMARY_SECONDS // 60, raw_day)
                written, empty, last_summary = 0, 0, time.monotonic()
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                    log.warning("El tópico %s todavía no existe; sigo esperando mensajes", topic)
                    time.sleep(5)
                else:
                    log.error("Kafka reportó un error: %s", msg.error())
                continue
            if msg.value() is None:
                empty += 1
                consumer.store_offsets(message=msg)
                continue

            day = datetime.now(MEXICO).date().isoformat()
            if day != raw_day:
                if raw:
                    raw.close()
                raw_day = day
                raw = (COMMENTS_RAW / f"{day}.log").open("a", encoding="utf-8")

            # Una línea por mensaje. Un JSON válido no lleva saltos de línea
            # literales dentro de sus textos (van escapados como \n), así que
            # cambiarlos por espacios no altera el contenido.
            line = msg.value().decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
            try:
                raw.write(line + "\n")
                raw.flush()
            except OSError as e:
                log.error("No se pudo escribir en el crudo %s.log: %s", raw_day, e)
                sys.exit(1)
            consumer.store_offsets(message=msg)
            written += 1
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("Error inesperado al leer de Kafka")
        sys.exit(1)
    finally:
        log.info("fin; guardados_desde_el_último_resumen=%d", written)
        consumer.close()
        if raw:
            raw.close()


if __name__ == "__main__":
    main()
