"""lyrics_processor: sentimiento y emoción de la letra de cada canción.

Corre cada hora con cron, justo después de lyrics_fetcher. Si no hay
letras pendientes, termina sin cargar el modelo.

  1. Pendientes: canciones con lyrics_status = 'found' cuyo model_version
     es distinto al actual: letras nuevas, letras cuya versión cambió (el
     fetcher borra model_version) o todas, si cambia el modelo. Así el
     índice de ánimo siempre compara letras analizadas con el mismo modelo
     (RFC, sección 7 c).
  2. Lee la versión elegida (lrclib_id) de raw/lyrics/<id de la URI>.json.
  3. La divide en estrofas (bloques separados por una línea vacía). El
     modelo trunca a 128 tokens, así que una letra completa perdería todo
     lo que pasa de la primera estrofa; un bloque de más de 8 líneas se
     corta en pedazos de 4.
  4. Analiza cada estrofa y promedia las probabilidades ponderando por el
     número de palabras de cada estrofa (el coro que se repite pesa más,
     como pesa al escucharla). lyrics_sentiment es el más probable.
  5. lyrics_emotion es la emoción que domina más palabras sin contar
     "others"; si todas las estrofas son "others", queda "others".
  6. lyrics_language: "es" o "en", según qué palabras comunes de cada
     idioma aparecen más. El modelo es para español; la columna permite
     ver cuántas letras en inglés hay y tratarlas aparte.

Una canción, una transacción. Si se corre dos veces, la segunda no
encuentra pendientes.
"""

import json
import re
import sys
import time
from collections import Counter

import psycopg

from common.jobs import RAW_DIR, pg_params, setup_logging, single_run
from transform.match import lyrics_text
from transform.normalize import normalize_text

JOB = "lyrics_processor"
LYRICS_RAW = RAW_DIR / "lyrics"
MAX_BLOCK_LINES = 8
CHUNK_LINES = 4

_BLANK_LINE = re.compile(r"\n\s*\n")
_WORD = re.compile(r"[a-zñ']+")
# Palabras muy comunes que casi no se comparten entre los dos idiomas. No
# incluye "baby" ni "oh", que aparecen igual en las letras en español.
_SPANISH = frozenset("de la que el en y los se mi te tu una por con para lo es yo pero como "
                     "si ya le las del al mas porque cuando esta todo nada quiero".split())
_ENGLISH = frozenset("the and you to my it is on your that of for i'm don't be we all so what "
                     "with this just like know".split())

PENDING_SQL = """
    SELECT song_key, spotify_uri, track_name, lrclib_id
    FROM dim_song
    WHERE lyrics_status = 'found' AND model_version IS DISTINCT FROM %s
    ORDER BY song_key
"""

SAVE_SQL = """
    UPDATE dim_song SET lyrics_language = %(language)s, lyrics_sentiment = %(sentiment)s,
           lyrics_p_pos = %(p_pos)s, lyrics_p_neu = %(p_neu)s, lyrics_p_neg = %(p_neg)s,
           lyrics_emotion = %(emotion)s, model_version = %(model_version)s, updated_at = now()
    WHERE song_key = %(song_key)s AND lrclib_id = %(lrclib_id)s
"""

# La versión elegida ya no está en el crudo: que lyrics_fetcher la vuelva a buscar.
RESET_SQL = """
    UPDATE dim_song SET lyrics_status = NULL, lrclib_id = NULL, model_version = NULL,
           updated_at = now()
    WHERE song_key = %s
"""


def split_stanzas(text: str) -> list[str]:
    """Estrofas de una letra: bloques separados por línea vacía, de 8 líneas o menos."""
    stanzas = []
    for block in _BLANK_LINE.split(text.replace("\r\n", "\n")):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) <= MAX_BLOCK_LINES:
            chunks = [lines] if lines else []
        else:
            chunks = [lines[i:i + CHUNK_LINES] for i in range(0, len(lines), CHUNK_LINES)]
        stanzas.extend("\n".join(chunk) for chunk in chunks)
    return stanzas


def word_count(text: str) -> int:
    return len(_WORD.findall(normalize_text(text)))


def guess_language(text: str) -> str:
    """"es" o "en" según las palabras comunes de cada idioma (empate: "es")."""
    words = _WORD.findall(normalize_text(text))
    spanish = sum(w in _SPANISH for w in words)
    english = sum(w in _ENGLISH for w in words)
    return "en" if english > spanish else "es"


def combine(stanzas: list[str], analyses: list) -> dict:
    """Junta el análisis de las estrofas en el de la canción (promedio ponderado por palabras)."""
    weights = [max(word_count(s), 1) for s in stanzas]
    total = sum(weights)
    p_pos = sum(w * a.p_pos for w, a in zip(weights, analyses)) / total
    p_neu = sum(w * a.p_neu for w, a in zip(weights, analyses)) / total
    p_neg = sum(w * a.p_neg for w, a in zip(weights, analyses)) / total
    probas = {"POS": p_pos, "NEU": p_neu, "NEG": p_neg}
    emotion_weight = Counter()
    for w, a in zip(weights, analyses):
        if a.emotion != "others":
            emotion_weight[a.emotion] += w
    emotion = (max(sorted(emotion_weight), key=emotion_weight.__getitem__)
               if emotion_weight else "others")
    return {"sentiment": max(probas, key=probas.__getitem__), "p_pos": round(p_pos, 4),
            "p_neu": round(p_neu, 4), "p_neg": round(p_neg, 4), "emotion": emotion}


def find_record(spotify_uri: str, lrclib_id: int) -> dict | None:
    path = LYRICS_RAW / f"{spotify_uri.rsplit(':', 1)[-1]}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    for query in payload.get("queries", []):
        for record in query.get("results", []):
            if isinstance(record, dict) and record.get("id") == lrclib_id:
                return record
    return None


def run(log, pg: dict) -> int:
    started = time.monotonic()
    stats = Counter()
    pending = []
    model_version = None
    try:
        from transform.sentiment import MODEL_VERSION
        model_version = MODEL_VERSION
        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            pending = conn.execute(PENDING_SQL, (model_version,)).fetchall()
            log.info("inicio; pendientes=%d modelo=%s", len(pending), model_version)
            if not pending:
                return 0
            from transform.sentiment import SentimentModel
            loaded = time.monotonic()
            model = SentimentModel()
            log.info("modelo cargado en %.1fs", time.monotonic() - loaded)
            for song_key, uri, track_name, lrclib_id in pending:
                record = find_record(uri, lrclib_id)
                if record is None:
                    with conn.transaction():
                        conn.execute(RESET_SQL, (song_key,))
                    stats["crudo_faltante"] += 1
                    log.warning("«%s»: la versión %s no está en el crudo; se vuelve a buscar",
                                track_name, lrclib_id)
                    continue
                text = lyrics_text(record)
                stanzas = split_stanzas(text)
                if not stanzas:
                    stats["sin_texto"] += 1
                    continue
                result = combine(stanzas, model.analyze(stanzas))
                language = guess_language(text)
                with conn.transaction():
                    conn.execute(SAVE_SQL, {**result, "language": language,
                                            "model_version": model_version,
                                            "song_key": song_key, "lrclib_id": lrclib_id})
                stats["analizadas"] += 1
                stats["estrofas"] += len(stanzas)
                stats[f"idioma_{language}"] += 1
                stats[result["sentiment"]] += 1
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; se reintenta en la siguiente corrida")
    finally:
        log.info("fin; duracion=%.0fs pendientes=%d analizadas=%d estrofas=%d pos=%d neu=%d "
                 "neg=%d idioma_es=%d idioma_en=%d crudo_faltante=%d sin_texto=%d errores=%d",
                 time.monotonic() - started, len(pending), stats["analizadas"], stats["estrofas"],
                 stats["POS"], stats["NEU"], stats["NEG"], stats["idioma_es"],
                 stats["idioma_en"], stats["crudo_faltante"], stats["sin_texto"],
                 stats["errores"])
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
