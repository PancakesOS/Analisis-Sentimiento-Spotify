"""Etiquetado manual de comentarios para validar el modelo de sentimiento.

Criterio de éxito del RFC (sección 10): el modelo debe coincidir con una
persona en al menos 70 % de 100 comentarios etiquetados a mano.

Etiquetar (se puede salir y retomar; lo ya etiquetado no se vuelve a
mostrar):

    docker compose exec batch /app/.venv/bin/python -m scripts.label_sentiment

Ver el resultado sin etiquetar más:

    docker compose exec batch /app/.venv/bin/python -m scripts.label_sentiment --resultado

Cómo se hace:

  * La muestra es al azar pero reproducible: los comentarios no
    descartados, ordenados por el md5 de su comment_id.
  * Es a ciegas: nunca se muestra lo que dijo el modelo, para no
    influir en la etiqueta.
  * Solo se guarda el id del comentario, la etiqueta de la persona y la
    del modelo (tabla sentiment_label). El texto se muestra en la
    terminal y no se escribe en ningún lado, así que la política de 30
    días de YouTube se sigue cumpliendo.
  * Un comentario que no se puede etiquetar (otro idioma, solo una
    mención o un enlace) se salta y no cuenta para la meta.
"""

import argparse
import sys
import textwrap
from collections import Counter

import psycopg

from common.jobs import pg_params, setup_logging

JOB = "label_sentiment"
GOAL = 100
TARGET_AGREEMENT = 0.70
LABELS = ("POS", "NEU", "NEG")
NAMES = {"POS": "positivo", "NEU": "neutro", "NEG": "negativo", "SKIP": "saltado"}
KEYS = {"1": "POS", "2": "NEU", "3": "NEG", "s": "SKIP"}
MAX_CHARS = 900

CANDIDATES_SQL = """
    SELECT f.comment_id, f.text_original, f.sentiment, f.model_version,
           s.track_name, a.artist_name
    FROM fact_comment f
    JOIN dim_song s ON s.song_key = f.song_key
    JOIN dim_artist a ON a.artist_key = s.main_artist_key
    WHERE NOT f.discarded
      AND NOT EXISTS (SELECT 1 FROM sentiment_label l WHERE l.comment_id = f.comment_id)
    ORDER BY md5(f.comment_id)
    LIMIT %s
"""

SAVE_SQL = """
    INSERT INTO sentiment_label (comment_id, human_sentiment, model_sentiment, model_version)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (comment_id) DO UPDATE
    SET human_sentiment = EXCLUDED.human_sentiment, labeled_at = now()
"""


def agreement(pairs: list[tuple[str, str]]) -> dict:
    """Métricas de coincidencia entre la persona y el modelo.

    pairs: (etiqueta de la persona, etiqueta del modelo), sin los SKIP.
    Regresa n, la coincidencia, la kappa de Cohen (coincidencia corregida
    por azar), la precisión y la exhaustividad de cada clase y la matriz
    de confusión {(persona, modelo): conteo}.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "accuracy": None, "kappa": None, "per_class": {}, "matrix": {}}
    matrix = Counter(pairs)
    hits = sum(matrix[(c, c)] for c in LABELS)
    accuracy = hits / n
    human = Counter(h for h, _ in pairs)
    model = Counter(m for _, m in pairs)
    expected = sum(human[c] * model[c] for c in LABELS) / (n * n)
    kappa = (accuracy - expected) / (1 - expected) if expected < 1 else None
    per_class = {}
    for c in LABELS:
        tp = matrix[(c, c)]
        per_class[c] = {
            "precision": tp / model[c] if model[c] else None,
            "recall": tp / human[c] if human[c] else None,
            "human": human[c],
            "model": model[c],
        }
    return {"n": n, "accuracy": accuracy, "kappa": kappa, "per_class": per_class,
            "matrix": dict(matrix)}


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} %"


def labeled_counts(conn) -> tuple[int, int]:
    """(etiquetados que cuentan, saltados)."""
    valid, skipped = conn.execute(
        "SELECT count(*) FILTER (WHERE human_sentiment <> 'SKIP'), "
        "count(*) FILTER (WHERE human_sentiment = 'SKIP') FROM sentiment_label").fetchone()
    return valid, skipped


def report(conn, log) -> None:
    pairs = conn.execute(
        "SELECT human_sentiment, model_sentiment FROM sentiment_label "
        "WHERE human_sentiment <> 'SKIP'").fetchall()
    _, skipped = labeled_counts(conn)
    m = agreement([tuple(p) for p in pairs])
    if m["n"] == 0:
        print("Todavía no hay comentarios etiquetados.")
        return
    status = "cumple" if m["accuracy"] >= TARGET_AGREEMENT else "no cumple"
    print(f"\nComentarios etiquetados: {m['n']} (más {skipped} saltados)")
    print(f"Coincidencia con el modelo: {fmt_pct(m['accuracy'])} "
          f"(meta del RFC: {TARGET_AGREEMENT * 100:.0f} %, {status})")
    if m["kappa"] is not None:
        print(f"Kappa de Cohen: {m['kappa']:.2f} (0 = lo que daría el azar, 1 = coincidencia perfecta)")
    print("\nPor clase      precisión   exhaustividad   persona   modelo")
    for c in LABELS:
        pc = m["per_class"][c]
        print(f"  {NAMES[c]:<12} {fmt_pct(pc['precision']):>9}   {fmt_pct(pc['recall']):>13}"
              f"   {pc['human']:>7}   {pc['model']:>6}")
    print("\nMatriz de confusión (filas: persona; columnas: modelo)")
    print("               " + "".join(f"{NAMES[c]:>10}" for c in LABELS))
    for h in LABELS:
        print(f"  {NAMES[h]:<12} " + "".join(f"{m['matrix'].get((h, c), 0):>10}" for c in LABELS))
    if m["n"] < GOAL:
        print(f"\nFaltan {GOAL - m['n']} para la meta de {GOAL}.")
    log.info("resultado; etiquetados=%d saltados=%d coincidencia=%.4f kappa=%s",
             m["n"], skipped, m["accuracy"],
             "—" if m["kappa"] is None else f"{m['kappa']:.4f}")


def show(row, done: int, goal: int) -> None:
    _, text, _, _, track, artist = row
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " […]"
    body = "\n".join(textwrap.fill(line, 88) if line.strip() else "" for line in text.splitlines())
    print(f"Comentario {done + 1} de {goal}   ·   {track} — {artist}\n")
    print(textwrap.indent(body, "    "))
    print("\n  1 positivo   2 neutro   3 negativo   s saltar (otro idioma, no se entiende)")
    print("  u deshacer el anterior   q salir")


def label(conn, log, goal: int) -> None:
    valid, skipped = labeled_counts(conn)
    log.info("inicio; etiquetados=%d saltados=%d meta=%d", valid, skipped, goal)
    history: list[str] = []
    new = 0
    while valid < goal:
        batch = conn.execute(CANDIDATES_SQL, (goal - valid + 50,)).fetchall()
        if not batch:
            print("No quedan comentarios por etiquetar.")
            break
        for row in batch:
            if valid >= goal:
                break
            while True:
                show(row, valid, goal)
                answer = input("\n> ").strip().lower()
                if answer in KEYS or answer in ("u", "q"):
                    break
            if answer == "q":
                log.info("fin; nuevos=%d etiquetados=%d (salida a medias)", new, valid)
                report(conn, log)
                return
            if answer == "u":
                if history:
                    last = history.pop()
                    was = conn.execute("DELETE FROM sentiment_label WHERE comment_id = %s "
                                       "RETURNING human_sentiment", (last,)).fetchone()
                    if was and was[0] != "SKIP":
                        valid -= 1
                    new -= 1
                break   # se vuelve a pedir la tanda: el deshecho aparece primero (orden md5)
            comment_id, _, model_sentiment, model_version, _, _ = row
            conn.execute(SAVE_SQL, (comment_id, KEYS[answer], model_sentiment, model_version))
            history.append(comment_id)
            new += 1
            if KEYS[answer] != "SKIP":
                valid += 1
    log.info("fin; nuevos=%d etiquetados=%d", new, valid)
    report(conn, log)


def main() -> None:
    parser = argparse.ArgumentParser(description="Etiquetado manual para validar el sentimiento")
    parser.add_argument("--resultado", action="store_true", help="solo mostrar el resultado")
    parser.add_argument("--meta", type=int, default=GOAL, help=f"comentarios a etiquetar ({GOAL})")
    args = parser.parse_args()
    log = setup_logging(JOB)
    # El log va solo al archivo: en la terminal estorbaría al etiquetar.
    log.handlers = [h for h in log.handlers if getattr(h, "stream", None) is not sys.stdout]
    with psycopg.connect(**pg_params(), connect_timeout=10, autocommit=True) as conn:
        try:
            if args.resultado:
                report(conn, log)
            else:
                label(conn, log, args.meta)
        except (KeyboardInterrupt, EOFError):
            log.info("fin; interrumpido (lo etiquetado ya quedó guardado)")
            print("\nSalida. Lo etiquetado ya quedó guardado.")


if __name__ == "__main__":
    main()
