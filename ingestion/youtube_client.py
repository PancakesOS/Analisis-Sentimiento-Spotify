"""youtube_client: la única puerta a la YouTube Data API v3.

Todas las llamadas pasan por YouTubeClient.call(), que:

  1. Antes de llamar, suma el costo en etl_quota_usage, en una
     transacción con un candado de PostgreSQL: si dos trabajos llaman a la
     vez, la cuenta no se pierde. Si con esa llamada el día pasaría de
     DAILY_LIMIT (9,000 de las 10,000 unidades), no llama y lanza
     QuotaExceeded. La unidad se cuenta antes de llamar porque Google la
     cobra aunque la respuesta sea un error.
  2. Hace la petición HTTP con la librería estándar y reintenta dos veces
     los errores temporales (5xx o sin respuesta); cada reintento también
     se cuenta.
  3. Convierte los errores conocidos en excepciones: cuota agotada,
     comentarios desactivados, video inexistente, página inválida.

El día de la cuota es el de la hora del Pacífico, porque la cuota de
YouTube se reinicia a medianoche de esa zona (02:00 en la Ciudad de
México). La API key sale de YOUTUBE_API_KEY y nunca se escribe en un log.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

API_URL = "https://www.googleapis.com/youtube/v3/"
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
DAILY_LIMIT = 9_000          # de 10,000: deja margen por si algo se cuenta de menos
DAILY_QUOTA = 10_000
COSTS = {"search.list": 100, "commentThreads.list": 1, "videos.list": 1}
QUOTA_LOCK_ID = 7_340_001    # pg_advisory_xact_lock: serializa la cuenta de la cuota
RETRIES = 2
TIMEOUT_SECONDS = 30


class YouTubeError(Exception):
    """Error de la API que no tiene una excepción más específica."""

    def __init__(self, message: str, status: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason


class QuotaExceeded(YouTubeError):
    """La cuota del día ya no alcanza (nuestro tope o el de Google)."""


class ApiKeyError(YouTubeError):
    """La API key no es válida o no tiene habilitada la YouTube Data API: no tiene caso seguir."""


class CommentsDisabled(YouTubeError):
    """El video tiene los comentarios desactivados."""


class VideoNotFound(YouTubeError):
    """El video no existe o es privado."""


class InvalidPageToken(YouTubeError):
    """El pageToken guardado ya no es válido."""


class NetworkError(YouTubeError):
    """No hubo respuesta completa de la API: sin red, sin DNS, conexión cortada o respuesta a medias."""


# Fallas de red que vale la pena reintentar. HTTPError también es URLError,
# pero se atiende antes, porque esa sí es una respuesta de la API.
TRANSIENT_ERRORS = (urllib.error.URLError, http.client.HTTPException, ConnectionError,
                    TimeoutError, json.JSONDecodeError)


def quota_date(now: datetime | None = None):
    return (now or datetime.now(QUOTA_TZ)).astimezone(QUOTA_TZ).date()


class YouTubeClient:
    """Cliente con su propia conexión a PostgreSQL (autocommit) para la cuota.

    Usa su propia conexión para que la cuenta de la cuota quede guardada
    aunque el trabajo que llama haga ROLLBACK de sus datos.
    """

    def __init__(self, pg: dict, api_key: str | None = None, daily_limit: int = DAILY_LIMIT):
        self.api_key = api_key or os.environ["YOUTUBE_API_KEY"]
        self.daily_limit = daily_limit
        self.conn = psycopg.connect(**pg, connect_timeout=10, autocommit=True)
        self.units_spent = 0
        self.calls = 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "YouTubeClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def used_today(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(sum(units), 0) FROM etl_quota_usage WHERE quota_date = %s",
            (quota_date(),),
        ).fetchone()
        return int(row[0])

    def _reserve(self, method: str) -> None:
        cost = COSTS[method]
        day = quota_date()
        with self.conn.transaction():
            self.conn.execute("SELECT pg_advisory_xact_lock(%s)", (QUOTA_LOCK_ID,))
            used = self.conn.execute(
                "SELECT COALESCE(sum(units), 0) FROM etl_quota_usage WHERE quota_date = %s", (day,)
            ).fetchone()[0]
            if used + cost > self.daily_limit:
                raise QuotaExceeded(
                    f"cuota del día agotada ({used}/{DAILY_QUOTA}; tope propio {self.daily_limit})",
                    reason="tope_propio")
            self.conn.execute(
                """
                INSERT INTO etl_quota_usage (quota_date, method, units, calls)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (quota_date, method)
                DO UPDATE SET units = etl_quota_usage.units + EXCLUDED.units,
                              calls = etl_quota_usage.calls + 1, updated_at = now()
                """,
                (day, method, cost),
            )
        self.units_spent += cost
        self.calls += 1

    def call(self, method: str, **params) -> dict:
        """Llama a un método de la API (p. ej. "search.list") y regresa el JSON."""
        resource = method.split(".")[0]
        query = {k: v for k, v in params.items() if v is not None}
        query["key"] = self.api_key
        url = API_URL + resource + "?" + urllib.parse.urlencode(query)

        for attempt in range(RETRIES + 1):
            self._reserve(method)
            try:
                with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                error = self._parse_error(e)
                if e.code >= 500 and attempt < RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                raise error
            except TRANSIENT_ERRORS as e:
                if attempt < RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                raise NetworkError(f"sin respuesta de la API: {type(e).__name__}: "
                                   f"{self._scrub(str(e))}") from None
        raise NetworkError("sin respuesta de la API")

    def _scrub(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text

    def _parse_error(self, e: urllib.error.HTTPError) -> YouTubeError:
        try:
            body = json.loads(e.read().decode("utf-8", errors="replace"))
            err = body.get("error", {})
            reasons = [x.get("reason") for x in err.get("errors", [])]
            reason = reasons[0] if reasons else None
            message = self._scrub(err.get("message", ""))[:300]
        except (ValueError, AttributeError):
            reason, message = None, ""
        text = f"HTTP {e.code} {reason or ''}: {message}".strip()
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            return QuotaExceeded(text, e.code, reason)
        if reason in ("keyInvalid", "keyExpired", "accessNotConfigured", "ipRefererBlocked") \
                or "API key not valid" in message or "API key expired" in message:
            return ApiKeyError(text + " (revisa YOUTUBE_API_KEY en .env)", e.code, reason)
        if reason == "commentsDisabled":
            return CommentsDisabled(text, e.code, reason)
        if reason in ("videoNotFound", "notFound") or e.code == 404:
            return VideoNotFound(text, e.code, reason)
        if reason == "invalidPageToken":
            return InvalidPageToken(text, e.code, reason)
        return YouTubeError(text, e.code, reason)
