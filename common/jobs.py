"""Utilidades que comparten todos los trabajos: log, conexión a PostgreSQL y candado.

Formato del log (RFC, sección 7 f):

    fecha hora zona NIVEL trabajo: mensaje

Cada trabajo escribe en /app/logs/<trabajo>.log y también en la salida
estándar. Cuando lo ejecuta cron, la salida estándar se descarta (ver
ingestion/crontab), así que lo que cuenta es el archivo.
"""

import fcntl
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APP_DIR = Path(os.environ.get("APP_DIR", "/app"))
LOG_DIR = APP_DIR / "logs"
DATA_DIR = APP_DIR / "data"
LANDING_DIR = DATA_DIR / "landing"
RAW_DIR = DATA_DIR / "raw"
STATE_DIR = DATA_DIR / "state"

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(job: str) -> logging.Logger:
    """Regresa el logger del trabajo, que escribe en logs/<trabajo>.log y en stdout."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATEFMT)
    logger = logging.getLogger(job)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        for handler in (logging.FileHandler(LOG_DIR / f"{job}.log", encoding="utf-8"),
                        logging.StreamHandler(sys.stdout)):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    return logger


def pg_params() -> dict:
    """Parámetros de conexión a PostgreSQL, tomados de las variables de entorno.

    Lanza KeyError si falta alguna; cada trabajo lo registra en su log.
    """
    return {
        "host": os.environ["POSTGRES_HOST"],
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }


@contextmanager
def single_run(job: str) -> Iterator[bool]:
    """Candado para que no corran dos instancias del mismo trabajo a la vez.

    Regresa True si consiguió el candado. Si otra corrida sigue en curso
    (por ejemplo, la del arranque del contenedor y la de cron en el mismo
    minuto), regresa False y el trabajo no debe hacer nada.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / f"{job}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
