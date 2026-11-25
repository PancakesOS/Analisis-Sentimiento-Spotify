"""Prueba de idempotencia: correr la carga dos veces da el mismo resultado.

    docker compose exec batch /app/.venv/bin/python -m scripts.check_idempotency

Corre dos veces seguidas cada trabajo que lee de la capa bronce (la zona
cruda) y escribe en las capas silver y gold, y compara el contenido de
cada tabla antes y después de la segunda corrida:

  1. Primera pasada: deja todo al día (si había algo pendiente, aquí se
     carga).
  2. Foto A: número de filas y huella (md5 de todas las filas) de cada
     tabla de silver, de gold y de control.etl_loaded_file.
  3. Segunda pasada: los mismos trabajos, en el mismo orden.
  4. Foto B y comparación: si son iguales, termina con OK.

Los trabajos que llaman a APIs externas (lyrics_fetcher, video_finder,
stats_snapshot y comments_producer) no entran: traen datos en vivo, así
que una segunda llamada puede traer datos nuevos de verdad. Su
idempotencia está en su estado: no repiten lo que ya hicieron (sha256,
marcas de agua, video_checked_at).

Si comments_producer trajo comentarios nuevos entre las dos pasadas, la
segunda los carga y la prueba lo reporta como diferencia en fact_comment;
basta con volver a correrla.
"""

import subprocess
import sys
import time

import psycopg

from common.jobs import pg_params, setup_logging

JOB = "check_idempotency"
PYTHON = sys.executable

# En el orden del crontab: bronce → silver → gold.
JOBS = [
    "ingestion.chart_loader",          # raw/charts → dimensiones y fact_chart_week
    "transform.lyrics_processor",      # raw/lyrics → letra y sentimiento en dim_song
    "transform.comments_processor",    # raw/comments → fact_comment
    "transform.aggregate_week",        # fact_comment → song_week_reaction
    "transform.retention",             # borra lo de 29 días o más
    "delivery.model.train",            # v_model_dataset → model_run y model_prediction
]

TABLES_SQL = """
    SELECT table_schema, table_name FROM information_schema.tables
    WHERE table_type = 'BASE TABLE'
      AND (table_schema IN ('silver', 'gold')
           OR (table_schema = 'control' AND table_name = 'etl_loaded_file'))
    ORDER BY table_schema, table_name
"""


def snapshot(conn) -> dict[str, tuple[int, str]]:
    """{esquema.tabla: (filas, huella)}. La huella es el md5 de los md5 de cada fila, ordenados."""
    result = {}
    for schema, table in conn.execute(TABLES_SQL).fetchall():
        rows, digest = conn.execute(
            f'SELECT count(*), md5(COALESCE(string_agg(h, \'\' ORDER BY h), \'\')) '
            f'FROM (SELECT md5(t::text) AS h FROM "{schema}"."{table}" t) x').fetchone()
        result[f"{schema}.{table}"] = (rows, digest)
    return result


def run_jobs(log, label: str) -> None:
    for job in JOBS:
        started = time.monotonic()
        proc = subprocess.run([PYTHON, "-m", job], cwd="/app", capture_output=True, text=True)
        status = "ok" if proc.returncode == 0 else f"código {proc.returncode}"
        log.info("%s: %s (%s, %.1fs)", label, job, status, time.monotonic() - started)


def main() -> None:
    log = setup_logging(JOB)
    started = time.monotonic()
    log.info("inicio; trabajos=%d", len(JOBS))
    with psycopg.connect(**pg_params(), connect_timeout=10, autocommit=True) as conn:
        run_jobs(log, "primera pasada")
        before = snapshot(conn)
        run_jobs(log, "segunda pasada")
        after = snapshot(conn)

    diffs = []
    print(f"\n{'Tabla':<32}{'Filas':>10}  Huella (md5)                      Resultado")
    for name in sorted(before):
        rows, digest = after.get(name, (None, None))
        same = before[name] == (rows, digest)
        print(f"{name:<32}{rows:>10,}  {digest}  {'igual' if same else 'DISTINTA'}")
        if not same:
            diffs.append(f"{name}: antes {before[name][0]:,} filas ({before[name][1][:8]}), "
                         f"después {rows:,} ({digest[:8]})")
    for d in diffs:
        log.warning("diferencia: %s", d)
    result = "OK" if not diffs else "DIFERENCIAS"
    log.info("fin; duracion=%.1fs tablas=%d diferencias=%d resultado=%s",
             time.monotonic() - started, len(before), len(diffs), result)
    print(f"\n{result}")
    sys.exit(0 if not diffs else 1)


if __name__ == "__main__":
    main()
