"""aggregate_week: recalcula song_week_reaction a partir de fact_comment.

Corre cada hora con cron, justo después de comments_processor. Es lo que
usan las preguntas de negocio, y lo único que queda de los comentarios
después de los 30 días.

  1. Descartados: marca discarded = true en los comentarios cuyo video ya
     no es el de su canción (se corrigió el video a mano) o cuya canción
     tiene la letra en inglés (queda fuera del análisis de comentarios,
     igual que de las preguntas de letras), y lo quita si deja de
     cumplirse. Las parejas canción–semana que cambian se recalculan; una
     pareja que se queda sin comentarios válidos desaparece.
  2. Parejas nuevas: una pareja (semana, canción) se recalcula si tiene
     comentarios con ingested_at posterior a song_week_reaction.
     last_ingested_at, o si todavía no tiene fila. No hace falta un
     archivo de estado: la marca está en la misma tabla.
  3. Recalcula cada pareja desde cero (DELETE + INSERT) con todos sus
     comentarios no descartados, todo en una transacción. Si se corre dos
     veces, la segunda no encuentra nada que recalcular.

Medidas por pareja:
  n_comments        comentarios no descartados;
  pct_pos/neu/neg   proporción de comentarios con cada sentimiento;
  avg_score         promedio de p_pos - p_neg, de -1 a 1;
  dominant_emotion  la emoción más frecuente sin contar "others" (casi
                    siempre es la mayoría y no dice nada del tono); si
                    todos son "others", queda "others".

Retención: retention borra los comentarios con más de 29 días de
ingeridos, pero sus parejas ya no se recalculan, porque no reciben
comentarios nuevos: los de una semana llegan durante esa semana (cada
hora) o de una sola vez en la carga histórica de su video. Así la fila
agregada se conserva aunque el texto ya no exista.
"""

import sys
import time

import psycopg

from common.jobs import pg_params, setup_logging, single_run

JOB = "aggregate_week"

# Un comentario no cuenta si su video ya no es el de su canción o si la
# canción tiene la letra en inglés. Las dos condiciones nunca dan NULL.
DISCARD_RULE = ("(s.youtube_video_id IS DISTINCT FROM f.video_id"
                " OR s.lyrics_language IS NOT DISTINCT FROM 'en')")

DISCARD_SQL = f"""
    UPDATE fact_comment f
    SET discarded = {DISCARD_RULE}
    FROM dim_song s
    WHERE s.song_key = f.song_key
      AND f.discarded <> {DISCARD_RULE}
    RETURNING (SELECT to_char(d.chart_week_end, 'YYYYMMDD')::integer
               FROM dim_date d WHERE d.date_key = f.date_key) AS week_key,
              f.song_key
"""

STALE_SQL = """
    SELECT to_char(d.chart_week_end, 'YYYYMMDD')::integer AS week_key, f.song_key
    FROM fact_comment f
    JOIN dim_date d ON d.date_key = f.date_key
    LEFT JOIN song_week_reaction r
           ON r.week_key = to_char(d.chart_week_end, 'YYYYMMDD')::integer
          AND r.song_key = f.song_key
    WHERE NOT f.discarded
    GROUP BY 1, 2, r.last_ingested_at
    HAVING r.last_ingested_at IS NULL OR max(f.ingested_at) > r.last_ingested_at
"""

DELETE_SQL = """
    DELETE FROM song_week_reaction r
    USING unnest(%s::integer[], %s::integer[]) AS p(week_key, song_key)
    WHERE r.week_key = p.week_key AND r.song_key = p.song_key
"""

INSERT_SQL = """
    INSERT INTO song_week_reaction (week_key, song_key, n_comments, pct_pos, pct_neu, pct_neg,
                                    avg_score, dominant_emotion, updated_at, last_ingested_at)
    SELECT p.week_key, f.song_key,
           count(*),
           round(avg((f.sentiment = 'POS')::int), 4),
           round(avg((f.sentiment = 'NEU')::int), 4),
           round(avg((f.sentiment = 'NEG')::int), 4),
           round(avg(f.score), 4),
           COALESCE(mode() WITHIN GROUP (ORDER BY f.emotion)
                        FILTER (WHERE f.emotion IS NOT NULL AND f.emotion <> 'others'),
                    'others'),
           now(),
           max(f.ingested_at)
    FROM unnest(%s::integer[], %s::integer[]) AS p(week_key, song_key)
    JOIN dim_date d ON d.chart_week_end = to_date(p.week_key::text, 'YYYYMMDD')
    JOIN fact_comment f ON f.date_key = d.date_key AND f.song_key = p.song_key
    WHERE NOT f.discarded
    GROUP BY p.week_key, f.song_key
"""


def run(log, pg: dict) -> int:
    started = time.monotonic()
    pairs, discarded_changes, written, errors = set(), 0, 0, 0
    log.info("inicio")
    try:
        with psycopg.connect(**pg, connect_timeout=10) as conn:
            # Todo es una sola transacción: COMMIT al salir del bloque sin errores.
            changed = conn.execute(DISCARD_SQL).fetchall()
            discarded_changes = len(changed)
            pairs.update(changed)
            pairs.update(conn.execute(STALE_SQL).fetchall())
            if pairs:
                weeks = [p[0] for p in sorted(pairs)]
                songs = [p[1] for p in sorted(pairs)]
                conn.execute(DELETE_SQL, (weeks, songs))
                written = conn.execute(INSERT_SQL, (weeks, songs)).rowcount
    except psycopg.OperationalError as e:
        errors += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        errors += 1
        log.exception("Error inesperado; no se cambió nada y se reintenta en la siguiente corrida")
    finally:
        weeks_touched = len({p[0] for p in pairs})
        log.info("fin; duracion=%.1fs parejas_recalculadas=%d filas_escritas=%d semanas=%d "
                 "cambios_de_descarte=%d errores=%d", time.monotonic() - started, len(pairs),
                 written, weeks_touched, discarded_changes, errors)
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
