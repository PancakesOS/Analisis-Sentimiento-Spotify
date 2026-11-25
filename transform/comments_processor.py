"""comments_processor: convierte el crudo de comentarios en filas de fact_comment.

Viene del processor de la Tarea 4 (checkpoint por byte, estado guardado
después del COMMIT). Corre cada hora con cron, a los 15 minutos, y
aggregate_week corre justo después.

  1. Lee solo lo nuevo de cada archivo raw/comments/AAAA-MM-DD.log: guarda
     en su estado hasta qué byte llegó en cada archivo. Solo toma líneas
     completas; si el consumer va a medio escribir una, se queda para la
     siguiente corrida.
  2. Valida cada línea y descarta, con su motivo:
       json_invalido      la línea no es un objeto JSON;
       campos_invalidos   falta un campo o tiene otro tipo, o la fecha no
                          es válida;
       vacio              el comentario no tiene texto;
       duplicado          el comment_id ya se procesó (Kafka entrega al
                          menos una vez y el poller puede repetir una
                          página);
       video_sin_cancion  el video ya no es el de ninguna canción (se
                          corrigió a mano y se eligió otro);
       cancion_en_ingles  la canción tiene la letra en inglés: queda fuera
                          del análisis de comentarios, igual que de las
                          preguntas de letras (el modelo es para español
                          y sus videos juntan comentarios de todo el
                          mundo). No se analiza, así que no gasta CPU.
  3. Calcula sentimiento y emoción con transform/sentiment.py, en lotes de
     64 comentarios.
  4. Inserta en fact_comment con INSERT … ON CONFLICT (comment_id) DO
     NOTHING: ningún comentario se cuenta dos veces. Cada bloque de
     CHUNK_LINES líneas es una transacción.
  5. Solo después del COMMIT guarda el byte hasta el que llegó. Si algo
     falla antes, la siguiente corrida repite el bloque y el ON CONFLICT
     evita duplicados.

El día del comentario (date_key) es su fecha de publicación en hora de
México; su semana del chart sale de dim_date. No escribe el texto de los
comentarios en su log (retención de 30 días de YouTube).

Si retention borra un archivo del crudo, su entrada del estado se quita.
"""

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

from common.jobs import RAW_DIR, STATE_DIR, pg_params, setup_logging, single_run

JOB = "comments_processor"
COMMENTS_RAW = RAW_DIR / "comments"
STATE_FILE = STATE_DIR / "comments_processor.json"
MEXICO = ZoneInfo("America/Mexico_City")
CHUNK_LINES = 2_000
MIN_DATE_KEY, MAX_DATE_KEY = 20050101, 20351231   # el rango de dim_date

# Para cada video, la canción a la que pertenece. Si dos canciones comparten
# video, la que apareció en el chart más reciente (y luego la de mejor lugar).
VIDEO_SONG_SQL = """
    SELECT youtube_video_id, song_key, excluded FROM (
        SELECT s.youtube_video_id, s.song_key, max(f.week_key) AS last_week, min(f.rank) AS best,
               COALESCE(s.lyrics_language = 'en', false) AS excluded
        FROM dim_song s
        LEFT JOIN fact_chart_week f ON f.song_key = s.song_key
        WHERE s.youtube_video_id IS NOT NULL
        GROUP BY s.song_key
    ) AS x
    ORDER BY youtube_video_id, last_week DESC NULLS LAST, best, song_key
"""

INSERT_SQL = """
    INSERT INTO fact_comment (comment_id, song_key, video_id, date_key, published_at,
                              text_original, like_count, reply_count, p_pos, p_neu, p_neg,
                              sentiment, emotion, model_version)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (comment_id) DO NOTHING
"""


class InvalidLine(Exception):
    """La línea no es un comentario con el formato del tópico."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_comment(line: str) -> dict:
    """Valida una línea del crudo y regresa el comentario listo para insertar.

    Solo lee los campos de la lista blanca del producer; cualquier otro
    campo se ignora.
    """
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        raise InvalidLine("json_invalido")
    if not isinstance(msg, dict):
        raise InvalidLine("json_invalido")
    comment_id, video_id = msg.get("comment_id"), msg.get("video_id")
    text, published = msg.get("text_original"), msg.get("published_at")
    if not (isinstance(comment_id, str) and comment_id and isinstance(video_id, str) and video_id
            and isinstance(text, str) and isinstance(published, str)):
        raise InvalidLine("campos_invalidos")
    like_count, reply_count = msg.get("like_count", 0), msg.get("reply_count", 0)
    if not (_is_count(like_count) and _is_count(reply_count)):
        raise InvalidLine("campos_invalidos")
    try:
        published_at = datetime.fromisoformat(published)
    except ValueError:
        raise InvalidLine("campos_invalidos")
    if published_at.tzinfo is None:
        raise InvalidLine("campos_invalidos")
    date_key = int(published_at.astimezone(MEXICO).strftime("%Y%m%d"))
    if not MIN_DATE_KEY <= date_key <= MAX_DATE_KEY:
        raise InvalidLine("campos_invalidos")
    text = text.replace("\x00", "").strip()   # PostgreSQL no acepta el carácter nulo
    if not text:
        raise InvalidLine("vacio")
    return {"comment_id": comment_id, "video_id": video_id, "text": text,
            "published_at": published_at, "date_key": date_key,
            "like_count": like_count, "reply_count": reply_count}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"offsets": {}}
    with STATE_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    """Escribe el estado de forma atómica: queda el anterior o el nuevo, nunca uno a medias."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


def new_lines(path, offset: int):
    """Líneas completas desde el byte offset: lista de (texto, byte donde termina)."""
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n") + 1
    lines, position = [], offset
    for raw in chunk[:end].split(b"\n")[:-1]:
        position += len(raw) + 1
        lines.append((raw.decode("utf-8", errors="replace"), position))
    return lines


def load_video_songs(conn) -> tuple[dict, set]:
    """({video_id: song_key}, videos de canciones en inglés que no se analizan).

    Si un video es de varias canciones, se queda con la de la semana más
    reciente y el mejor lugar.
    """
    video_song, excluded = {}, set()
    for video_id, song_key, is_excluded in conn.execute(VIDEO_SONG_SQL):
        if video_id not in video_song:
            video_song[video_id] = song_key
            if is_excluded:
                excluded.add(video_id)
    return video_song, excluded


def process_block(conn, model, model_version: str, block, video_song: dict, seen: set,
                  stats: Counter, log, excluded: frozenset | set = frozenset()) -> None:
    """Valida, analiza e inserta un bloque de líneas en una transacción."""
    candidates = []
    for line, _ in block:
        if not line.strip():
            continue
        try:
            comment = parse_comment(line)
        except InvalidLine as e:
            stats[e.reason] += 1
            continue
        if comment["comment_id"] in seen:
            stats["duplicado"] += 1
            continue
        seen.add(comment["comment_id"])
        song_key = video_song.get(comment["video_id"])
        if song_key is None:
            stats["video_sin_cancion"] += 1
            continue
        if comment["video_id"] in excluded:
            stats["cancion_en_ingles"] += 1
            continue
        comment["song_key"] = song_key
        candidates.append(comment)
    stats["leidos"] += len(block)
    if not candidates:
        return

    # Los que ya están en la base no se vuelven a analizar (ahorra CPU).
    existing = {r[0] for r in conn.execute(
        "SELECT comment_id FROM fact_comment WHERE comment_id = ANY(%s)",
        ([c["comment_id"] for c in candidates],))}
    stats["duplicado"] += len(existing)
    candidates = [c for c in candidates if c["comment_id"] not in existing]
    if not candidates:
        return

    started = time.monotonic()
    analyses = model.analyze([c["text"] for c in candidates])
    stats["segundos_modelo"] += time.monotonic() - started
    rows = [(c["comment_id"], c["song_key"], c["video_id"], c["date_key"], c["published_at"],
             c["text"], c["like_count"], c["reply_count"], round(a.p_pos, 4), round(a.p_neu, 4),
             round(a.p_neg, 4), a.sentiment, a.emotion, model_version)
            for c, a in zip(candidates, analyses)]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(INSERT_SQL, rows)
        inserted = cur.rowcount
    stats["insertados"] += inserted
    stats["duplicado"] += len(rows) - inserted
    stats["lotes"] += 1


def run(log, pg: dict) -> int:
    started = time.monotonic()
    stats = Counter()
    try:
        state = load_state()
        offsets = state.setdefault("offsets", {})
        files = sorted(COMMENTS_RAW.glob("*.log")) if COMMENTS_RAW.is_dir() else []
        # Archivos que retention ya borró: su entrada del estado sobra.
        for name in [n for n in offsets if not (COMMENTS_RAW / n).exists()]:
            del offsets[name]
        pending = []
        for path in files:
            offset = offsets.get(path.name, 0)
            size = path.stat().st_size
            if size < offset:
                log.warning("%s mide %d bytes, menos que lo ya procesado (%d); se procesa "
                            "desde el inicio", path.name, size, offset)
                offset = 0
            if size > offset:
                pending.append((path, offset))
        log.info("inicio; archivos=%d con_pendientes=%d bytes_pendientes=%d", len(files),
                 len(pending), sum(p.stat().st_size - o for p, o in pending))
        if not pending:
            save_state(state)
            return 0

        from transform.sentiment import MODEL_VERSION, SentimentModel
        loaded = time.monotonic()
        model = SentimentModel()
        log.info("modelo %s cargado en %.1fs", MODEL_VERSION, time.monotonic() - loaded)

        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            video_song, excluded = load_video_songs(conn)
            seen: set[str] = set()
            for path, offset in pending:
                lines = new_lines(path, offset)
                log.info("archivo=%s desde_byte=%d lineas_nuevas=%d", path.name, offset, len(lines))
                for i in range(0, len(lines), CHUNK_LINES):
                    block = lines[i:i + CHUNK_LINES]
                    process_block(conn, model, MODEL_VERSION, block, video_song, seen, stats, log,
                                  excluded)
                    offsets[path.name] = block[-1][1]
                    save_state(state)          # solo después del COMMIT del bloque
    except psycopg.OperationalError as e:
        stats["errores"] += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        stats["errores"] += 1
        log.exception("Error inesperado; el bloque se repite en la siguiente corrida")
    finally:
        reasons = ("duplicado", "vacio", "json_invalido", "campos_invalidos", "video_sin_cancion",
                   "cancion_en_ingles")
        discarded = sum(stats[r] for r in reasons)
        detail = ", ".join(f"{r}={stats[r]}" for r in reasons if stats[r])
        speed = stats["insertados"] / stats["segundos_modelo"] if stats["segundos_modelo"] else 0
        log.info("fin; duracion=%.0fs leidos=%d insertados=%d descartados=%d%s lotes=%d "
                 "comentarios_por_segundo=%.0f errores=%d", time.monotonic() - started,
                 stats["leidos"], stats["insertados"], discarded,
                 f" ({detail})" if detail else "", stats["lotes"], speed, stats["errores"])
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
