"""Identidad de una canción cuando Spotify le cambia la URI.

Spotify a veces cambia la URI de una canción que ya está en el chart, y
a veces regresa a la anterior. Ejemplo real: "Muévelo" aparece con una
URI la semana del 2026-01-08, con otra el 2026-01-15 y otra vez con la
primera el 2026-01-22. El chart sí la trata como la misma canción: la
fila con la URI nueva trae en previous_rank el lugar de la anterior, y
weeks_on_chart sigue contando.

Dos reglas, y en las dos se exige el mismo título normalizado y el mismo
artista principal, para no unir dos canciones distintas por coincidencia:

  1. previous_rank: una fila de la semana w es la misma canción que la
     fila de la semana w - 7 que ocupaba el lugar previous_rank.
  2. Reingreso: si la fila no estaba la semana anterior (previous_rank
     vacío) pero weeks_on_chart > 1, es la misma canción que la que tuvo
     weeks_on_chart - 1 en una semana anterior. Spotify sigue contando las
     semanas aunque la URI haya cambiado mientras la canción estaba fuera
     ("El Azul" de Junior H llega a 146, sale y reingresa con otra URI en
     la 147). Si Spotify reinicia la cuenta en 1, la trata como otra
     canción y aquí también queda separada.

  * find_previous_song() y find_reentry_song(): al cargar una semana,
    buscan a qué canción pertenece una URI nueva (semanas en orden).
  * reconcile(): une las canciones que quedaron partidas cuando las
    semanas se cargan en desorden (por ejemplo, una semana vieja que
    llega tarde). Recorre todo fact_chart_week y es idempotente: si no
    hay nada que unir, no cambia nada.
"""

import logging

import psycopg

# Pares (canción partida, canción con la que se une) que hay que unir.
# La fila c viene de la fila p según la regla 1 o la 2; son otra song_key
# pero la misma canción, y nunca aparecen juntas en una misma semana.
PAIRS_SQL = """
    WITH linked AS (
        -- Regla 1: p ocupaba la semana anterior el lugar previous_rank de c.
        SELECT c.song_key AS from_key, p.song_key AS into_key
        FROM fact_chart_week c
        JOIN dim_date dc ON dc.date_key = c.week_key
        JOIN dim_date dp ON dp.date = dc.date - 7
        JOIN fact_chart_week p ON p.week_key = dp.date_key AND p.rank = c.previous_rank
        UNION
        -- Regla 2: c reingresa y p es su aparición anterior (una semana menos en el chart).
        SELECT c.song_key, p.song_key
        FROM fact_chart_week c
        JOIN fact_chart_week p ON p.week_key < c.week_key AND p.weeks_on_chart = c.weeks_on_chart - 1
        WHERE c.previous_rank IS NULL AND c.weeks_on_chart > 1
    )
    SELECT DISTINCT l.from_key, l.into_key
    FROM linked l
    JOIN dim_song sc ON sc.song_key = l.from_key
    JOIN dim_song sp ON sp.song_key = l.into_key
    WHERE l.from_key <> l.into_key
      AND sc.track_name_norm = sp.track_name_norm
      AND sc.main_artist_key = sp.main_artist_key
      AND NOT EXISTS (
          SELECT 1 FROM fact_chart_week a
          JOIN fact_chart_week b ON b.week_key = a.week_key AND b.song_key = l.into_key
          WHERE a.song_key = l.from_key)
    ORDER BY 1, 2
"""

# Columnas que llenan las etapas siguientes (YouTube, LRCLIB, sentimiento):
# al unir, se conserva lo que ya tenga la canción que queda y se completa
# con lo de la que desaparece.
ENRICHMENT_COLUMNS = (
    "youtube_video_id", "video_match", "video_checked_at", "lyrics_status", "lyrics_checked_at",
    "lrclib_id", "lyrics_language", "lyrics_sentiment", "lyrics_p_pos", "lyrics_p_neu",
    "lyrics_p_neg", "lyrics_emotion", "model_version",
)

# Hechos que no se mueven solos: si la canción partida ya tiene filas en
# alguno, la unión se deja para revisión manual.
OTHER_FACTS = ("fact_comment", "fact_video_daily", "song_week_reaction")


def find_previous_song(cur: psycopg.Cursor, prev_week_key: int, previous_rank: int,
                       title_norm: str, main_artist_key: int) -> int | None:
    """song_key de la canción que la semana anterior estaba en previous_rank, si es la misma."""
    cur.execute(
        """
        SELECT s.song_key
        FROM fact_chart_week f JOIN dim_song s ON s.song_key = f.song_key
        WHERE f.week_key = %s AND f.rank = %s
          AND s.track_name_norm = %s AND s.main_artist_key = %s
        """,
        (prev_week_key, previous_rank, title_norm, main_artist_key),
    )
    row = cur.fetchone()
    return row[0] if row else None


def find_reentry_song(cur: psycopg.Cursor, week_key: int, weeks_on_chart: int,
                      title_norm: str, main_artist_key: int) -> int | None:
    """song_key de la misma canción que tuvo weeks_on_chart - 1 en una semana anterior."""
    if weeks_on_chart <= 1:
        return None
    cur.execute(
        """
        SELECT s.song_key
        FROM fact_chart_week f JOIN dim_song s ON s.song_key = f.song_key
        WHERE f.week_key < %s AND f.weeks_on_chart = %s
          AND s.track_name_norm = %s AND s.main_artist_key = %s
        ORDER BY f.week_key DESC
        LIMIT 1
        """,
        (week_key, weeks_on_chart - 1, title_norm, main_artist_key),
    )
    row = cur.fetchone()
    return row[0] if row else None


def merge_songs(cur: psycopg.Cursor, from_key: int, into_key: int) -> bool:
    """Une from_key en into_key. Regresa False si se dejó para revisión manual."""
    for table in OTHER_FACTS:
        cur.execute(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE song_key = %s)", (from_key,))
        if cur.fetchone()[0]:
            return False

    cur.execute(
        "SELECT song_key, max(week_key) FROM fact_chart_week WHERE song_key = ANY(%s) GROUP BY 1",
        ([from_key, into_key],),
    )
    last_week = dict(cur.fetchall())
    from_is_newer = last_week.get(from_key, 0) > last_week.get(into_key, 0)

    cur.execute("UPDATE fact_chart_week SET song_key = %s WHERE song_key = %s", (into_key, from_key))
    cur.execute("UPDATE song_uri SET song_key = %s WHERE song_key = %s", (into_key, from_key))

    # SCD tipo 1: los datos descriptivos son los de la semana más reciente.
    if from_is_newer:
        cur.execute(
            """
            UPDATE dim_song i
            SET track_name = f.track_name, track_name_norm = f.track_name_norm,
                main_artist_key = f.main_artist_key, label = f.label, label_norm = f.label_norm
            FROM dim_song f
            WHERE i.song_key = %s AND f.song_key = %s
            """,
            (into_key, from_key),
        )
        cur.execute("DELETE FROM song_artist WHERE song_key = %s", (into_key,))
        cur.execute("UPDATE song_artist SET song_key = %s WHERE song_key = %s", (into_key, from_key))
    else:
        cur.execute("DELETE FROM song_artist WHERE song_key = %s", (from_key,))

    enrichment = ", ".join(f"{c} = COALESCE(i.{c}, f.{c})" for c in ENRICHMENT_COLUMNS)
    cur.execute(
        f"""
        UPDATE dim_song i
        SET first_seen_week = LEAST(i.first_seen_week, f.first_seen_week), {enrichment},
            updated_at = now()
        FROM dim_song f
        WHERE i.song_key = %s AND f.song_key = %s
        """,
        (into_key, from_key),
    )
    cur.execute("DELETE FROM dim_song WHERE song_key = %s", (from_key,))
    return True


def reconcile(cur: psycopg.Cursor, log: logging.Logger | None = None) -> int:
    """Une todas las canciones partidas por un cambio de URI. Regresa cuántas unió."""
    merged, skipped = 0, set()
    while True:
        cur.execute(PAIRS_SQL)
        pairs = [pair for pair in cur.fetchall() if pair not in skipped]
        if not pairs:
            return merged
        from_key, into_key = pairs[0]
        if merge_songs(cur, from_key, into_key):
            merged += 1
        else:
            skipped.add((from_key, into_key))
            if log:
                log.warning("song_key %s parece la misma canción que %s con otra URI, pero ya "
                            "tiene comentarios o estadísticas; se deja para revisión manual",
                            from_key, into_key)
