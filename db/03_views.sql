-- =====================================================================
-- 03_views.sql · Vistas de las preguntas de negocio (RFC, sección 7 b)
-- =====================================================================
-- Una vista por pregunta de Laura (v_p1 … v_p7), más unas vistas de apoyo
-- que comparten. El dashboard solo lee de aquí; el modelo predictivo lee
-- v_model_dataset.
--
--   Apoyo:  emotion_es()        nombre en español de cada emoción
--           v_chart_week        semanas del chart cargadas
--           v_label             nombre para mostrar de cada sello
--           v_song              canción con artista, sello y letra
--           v_video_week        vistas ganadas por canción y semana
--           v_model_dataset     una fila por canción del top 50 y semana
--           v_song_longevity    semanas en el chart de cada canción
--   Preguntas: v_p1 … v_p7
--
-- Convenciones:
--   * Las semanas del chart van de viernes a jueves; week_key es el
--     date_key (AAAAMMDD) del jueves y week_end su fecha.
--   * recency = 1 es la semana más reciente cargada, 2 la anterior, etc.
--   * lyrics_score = lyrics_p_pos - lyrics_p_neg, de -1 a 1; avg_score es
--     lo mismo para los comentarios.
--   * Las vistas traen todas las semanas; el dashboard filtra la que
--     elige Laura (por omisión, la más reciente).
--
-- Se pueden volver a crear sin perder datos: solo son consultas.
-- =====================================================================

DROP VIEW IF EXISTS v_p7, v_p6, v_p5, v_p4, v_p3, v_p2, v_p1,
    v_song_longevity, v_model_dataset, v_video_week, v_song, v_label, v_chart_week CASCADE;


-- Nombre en español de cada emoción del modelo (para mostrarla).
CREATE OR REPLACE FUNCTION emotion_es(emotion text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE emotion
        WHEN 'joy'      THEN 'alegría'
        WHEN 'sadness'  THEN 'tristeza'
        WHEN 'anger'    THEN 'enojo'
        WHEN 'fear'     THEN 'miedo'
        WHEN 'surprise' THEN 'sorpresa'
        WHEN 'disgust'  THEN 'asco'
        WHEN 'others'   THEN 'sin emoción clara'
    END
$$;


-- ---------------------------------------------------------------------
-- v_chart_week: las semanas del chart cargadas.
-- ---------------------------------------------------------------------
CREATE VIEW v_chart_week AS
SELECT w.week_key,
       d.date                                       AS week_end,
       d.date - 6                                   AS week_start,
       row_number() OVER (ORDER BY w.week_key DESC) AS recency
FROM (SELECT DISTINCT week_key FROM fact_chart_week) AS w
JOIN dim_date d ON d.date_key = w.week_key;


-- ---------------------------------------------------------------------
-- v_label: un nombre para mostrar por sello normalizado. El CSV escribe
-- el mismo sello de varias formas ("Josa Records" y "Josa Records (Neton
-- Vega)"); se muestra la forma más común.
-- ---------------------------------------------------------------------
CREATE VIEW v_label AS
SELECT label_norm, mode() WITHIN GROUP (ORDER BY label) AS label_name
FROM dim_song
WHERE label_norm IS NOT NULL
GROUP BY label_norm;


-- ---------------------------------------------------------------------
-- v_song: la canción con lo que necesitan todas las preguntas.
--   * artists: todos sus artistas en orden, separados por coma.
--   * lyrics_score solo para letras en español: el modelo de sentimiento
--     es para español y en inglés da resultados poco confiables. Las
--     letras en inglés quedan fuera de las preguntas de letras, igual que
--     las canciones sin letra.
-- ---------------------------------------------------------------------
CREATE VIEW v_song AS
SELECT s.song_key,
       s.spotify_uri,
       s.track_name,
       a.artist_name AS main_artist,
       s.main_artist_key,
       (SELECT string_agg(a2.artist_name, ', ' ORDER BY sa.artist_order)
        FROM song_artist sa JOIN dim_artist a2 ON a2.artist_key = sa.artist_key
        WHERE sa.song_key = s.song_key)                                   AS artists,
       s.label,
       s.label_norm,
       COALESCE(l.label_name, s.label)                                    AS label_name,
       s.youtube_video_id,
       s.video_match,
       s.lyrics_status,
       s.lyrics_language,
       CASE WHEN s.lyrics_language = 'es' THEN s.lyrics_sentiment END     AS lyrics_sentiment,
       CASE WHEN s.lyrics_language = 'es' THEN s.lyrics_p_pos END         AS lyrics_p_pos,
       CASE WHEN s.lyrics_language = 'es' THEN s.lyrics_p_neu END         AS lyrics_p_neu,
       CASE WHEN s.lyrics_language = 'es' THEN s.lyrics_p_neg END         AS lyrics_p_neg,
       CASE WHEN s.lyrics_language = 'es'
            THEN s.lyrics_p_pos - s.lyrics_p_neg END                      AS lyrics_score,
       CASE WHEN s.lyrics_language = 'es' THEN s.lyrics_emotion END       AS lyrics_emotion,
       s.first_seen_week
FROM dim_song s
JOIN dim_artist a ON a.artist_key = s.main_artist_key
LEFT JOIN v_label l ON l.label_norm = s.label_norm;


-- ---------------------------------------------------------------------
-- v_video_week: vistas que ganó el video de cada canción en cada semana.
-- fact_video_daily guarda totales acumulados; la ganancia entre dos fotos
-- se le cuenta a la semana de la foto más nueva, así que la suma de una
-- semana va de la última foto anterior a la semana a la última de la
-- semana. La primera foto de un video no tiene contra qué compararse.
-- ---------------------------------------------------------------------
CREATE VIEW v_video_week AS
WITH diffs AS (
    SELECT v.song_key,
           to_char(d.chart_week_end, 'YYYYMMDD')::integer AS week_key,
           v.view_count - lag(v.view_count) OVER (PARTITION BY v.song_key, v.video_id
                                                  ORDER BY v.date_key) AS gained
    FROM fact_video_daily v
    JOIN dim_date d ON d.date_key = v.date_key
)
SELECT week_key, song_key,
       sum(gained)   AS views_gained,
       count(gained) AS days_with_stats
FROM diffs
GROUP BY week_key, song_key;


-- ---------------------------------------------------------------------
-- v_model_dataset: una fila por canción del top 50 y semana. Es la base
-- de la pregunta P2 y del modelo predictivo.
--   * went_up: la semana siguiente quedó en un lugar mejor (número menor).
--     Si salió del chart, cuenta como que no subió. NULL si la semana
--     siguiente todavía no está cargada.
--   * went_up_last_week: llegó a esta semana subiendo (el baseline).
-- ---------------------------------------------------------------------
CREATE VIEW v_model_dataset AS
SELECT c.week_key,
       w.week_end,
       c.song_key,
       s.track_name,
       s.main_artist,
       c.rank,
       c.previous_rank,
       c.weeks_on_chart,
       c.streams,
       n.rank                                                     AS next_rank,
       CASE WHEN nw.week_key IS NOT NULL
            THEN COALESCE(n.rank < c.rank, false) END             AS went_up,
       (c.previous_rank IS NOT NULL AND c.previous_rank > c.rank) AS went_up_last_week,
       COALESCE(r.n_comments, 0)                                  AS n_comments,
       r.pct_pos,
       r.pct_neg,
       r.avg_score,
       COALESCE(rp.n_comments, 0)                                 AS n_comments_prev_week,
       s.lyrics_score
FROM fact_chart_week c
JOIN v_chart_week w ON w.week_key = c.week_key
JOIN v_song s ON s.song_key = c.song_key
LEFT JOIN v_chart_week nw ON nw.week_end = w.week_end + 7
LEFT JOIN fact_chart_week n ON n.week_key = nw.week_key AND n.song_key = c.song_key
LEFT JOIN song_week_reaction r ON r.week_key = c.week_key AND r.song_key = c.song_key
LEFT JOIN song_week_reaction rp
       ON rp.song_key = c.song_key
      AND rp.week_key = to_char(w.week_end - 7, 'YYYYMMDD')::integer
WHERE c.rank <= 50;


-- ---------------------------------------------------------------------
-- v_song_longevity: semanas en el chart de cada canción (el máximo de
-- weeks_on_chart, que Spotify cuenta desde su primera aparición, aunque
-- sea anterior a la historia cargada). still_on_chart marca las que
-- siguen en la semana más reciente: su cuenta todavía puede crecer.
-- ---------------------------------------------------------------------
CREATE VIEW v_song_longevity AS
SELECT f.song_key,
       s.track_name,
       s.main_artist,
       s.lyrics_sentiment,
       s.lyrics_score,
       max(f.weeks_on_chart)                                          AS weeks_on_chart,
       min(f.rank)                                                    AS best_rank,
       max(f.week_key) = (SELECT max(week_key) FROM fact_chart_week)  AS still_on_chart
FROM fact_chart_week f
JOIN v_song s ON s.song_key = f.song_key
GROUP BY f.song_key, s.track_name, s.main_artist, s.lyrics_sentiment, s.lyrics_score;


-- =====================================================================
-- P1. ¿Qué canciones del top 50 de esta semana tienen la reacción más
--     positiva del público?
--   Solo canciones con 20 comentarios o más en la semana, para no premiar
--   a una canción con 3 comentarios. Se ordena por pct_pos.
-- =====================================================================
CREATE VIEW v_p1 AS
SELECT w.week_key,
       w.week_end,
       w.recency,
       f.rank,
       f.song_key,
       s.track_name,
       s.main_artist,
       s.artists,
       r.n_comments,
       r.pct_pos,
       r.pct_neu,
       r.pct_neg,
       r.avg_score,
       r.dominant_emotion,
       emotion_es(r.dominant_emotion) AS dominant_emotion_es,
       s.youtube_video_id
FROM fact_chart_week f
JOIN v_chart_week w ON w.week_key = f.week_key
JOIN v_song s ON s.song_key = f.song_key
JOIN song_week_reaction r ON r.week_key = f.week_key AND r.song_key = f.song_key
WHERE f.rank <= 50
  AND r.n_comments >= 20;


-- =====================================================================
-- P2. Las canciones que la gente recibe bien en YouTube, ¿suben en el
--     chart la semana siguiente?
--   Quintiles de pct_pos (1 = reacción más negativa) entre las canciones
--   del top 50 con 20 comentarios o más y semana siguiente conocida, y qué
--   porcentaje de cada quintil subió. La evaluación del modelo está en
--   model_run.
-- =====================================================================
CREATE VIEW v_p2 AS
WITH base AS (
    SELECT *, ntile(5) OVER (ORDER BY pct_pos, avg_score, week_key, song_key) AS quintile
    FROM v_model_dataset
    WHERE went_up IS NOT NULL AND n_comments >= 20
)
SELECT quintile,
       count(*)                          AS songs,
       count(DISTINCT week_key)          AS weeks,
       round(min(pct_pos), 4)            AS min_pct_pos,
       round(max(pct_pos), 4)            AS max_pct_pos,
       round(avg(pct_pos), 4)            AS avg_pct_pos,
       round(avg(went_up::integer), 4)   AS pct_went_up
FROM base
GROUP BY quintile;


-- =====================================================================
-- P3. ¿El top de México está más triste o más alegre que hace un mes?
--   Índice de ánimo de cada semana: Σ(streams × lyrics_score) / Σ(streams)
--   sobre las canciones del top 50 con letra analizada. change_4w es el
--   cambio contra 4 semanas antes. streams_coverage dice qué parte de los
--   streams del top 50 tiene letra analizada. Las participaciones por
--   emoción de la letra ayudan a explicar el cambio.
-- =====================================================================
CREATE VIEW v_p3 AS
WITH weekly AS (
    SELECT w.week_key,
           w.week_end,
           w.recency,
           sum(f.streams * s.lyrics_score) FILTER (WHERE s.lyrics_score IS NOT NULL)
               / NULLIF(sum(f.streams) FILTER (WHERE s.lyrics_score IS NOT NULL), 0) AS mood_index,
           sum(f.streams) FILTER (WHERE s.lyrics_score IS NOT NULL)::numeric
               / sum(f.streams)                                                     AS streams_coverage,
           count(*) FILTER (WHERE s.lyrics_score IS NOT NULL)                       AS songs_with_lyrics,
           count(*)                                                                 AS songs,
           sum(f.streams) FILTER (WHERE s.lyrics_sentiment = 'POS')::numeric
               / sum(f.streams)                                                     AS pct_streams_pos,
           sum(f.streams) FILTER (WHERE s.lyrics_sentiment = 'NEG')::numeric
               / sum(f.streams)                                                     AS pct_streams_neg,
           sum(f.streams) FILTER (WHERE s.lyrics_emotion = 'joy')::numeric
               / sum(f.streams)                                                     AS pct_streams_joy,
           sum(f.streams) FILTER (WHERE s.lyrics_emotion = 'sadness')::numeric
               / sum(f.streams)                                                     AS pct_streams_sadness
    FROM fact_chart_week f
    JOIN v_chart_week w ON w.week_key = f.week_key
    JOIN v_song s ON s.song_key = f.song_key
    WHERE f.rank <= 50
    GROUP BY w.week_key, w.week_end, w.recency
)
SELECT c.week_key,
       c.week_end,
       c.recency,
       round(c.mood_index, 4)                   AS mood_index,
       round(c.mood_index - p.mood_index, 4)    AS change_4w,
       round(c.streams_coverage, 4)             AS streams_coverage,
       c.songs_with_lyrics,
       c.songs,
       round(COALESCE(c.pct_streams_pos, 0), 4)     AS pct_streams_pos,
       round(COALESCE(c.pct_streams_neg, 0), 4)     AS pct_streams_neg,
       round(COALESCE(c.pct_streams_joy, 0), 4)     AS pct_streams_joy,
       round(COALESCE(c.pct_streams_sadness, 0), 4) AS pct_streams_sadness
FROM weekly c
LEFT JOIN weekly p ON p.week_end = c.week_end - 28;


-- =====================================================================
-- P4. ¿Qué artistas tienen letras negativas pero un público feliz?
--   Periodo: las últimas 12 semanas cargadas. Cuenta las canciones de
--   cada artista como principal o como invitado (tabla puente). La
--   reacción es el avg_score ponderado por número de comentarios.
--   negative_lyrics_happy_audience marca la respuesta: 2 canciones o más,
--   letra promedio < 0 y reacción promedio > 0.2.
-- =====================================================================
CREATE VIEW v_p4 AS
WITH recent_weeks AS (
    SELECT week_key FROM v_chart_week WHERE recency <= 12
), recent_songs AS (
    SELECT DISTINCT f.song_key
    FROM fact_chart_week f JOIN recent_weeks USING (week_key)
), reaction AS (
    SELECT r.song_key,
           sum(r.avg_score * r.n_comments) / sum(r.n_comments) AS reaction,
           sum(r.n_comments)                                  AS n_comments
    FROM song_week_reaction r JOIN recent_weeks USING (week_key)
    GROUP BY r.song_key
), per_artist AS (
    SELECT a.artist_key,
           a.artist_name,
           count(*)                                              AS songs,
           count(s.lyrics_score)                                 AS songs_with_lyrics,
           avg(s.lyrics_score)                                   AS avg_lyrics_score,
           count(re.song_key)                                    AS songs_with_reaction,
           sum(re.reaction * re.n_comments) / NULLIF(sum(re.n_comments), 0) AS avg_reaction,
           COALESCE(sum(re.n_comments), 0)                       AS n_comments
    FROM recent_songs rs
    JOIN song_artist sa ON sa.song_key = rs.song_key
    JOIN dim_artist a ON a.artist_key = sa.artist_key
    JOIN v_song s ON s.song_key = rs.song_key
    LEFT JOIN reaction re ON re.song_key = rs.song_key
    GROUP BY a.artist_key, a.artist_name
)
SELECT artist_key,
       artist_name,
       songs,
       songs_with_lyrics,
       round(avg_lyrics_score, 4) AS avg_lyrics_score,
       songs_with_reaction,
       round(avg_reaction, 4)     AS avg_reaction,
       n_comments,
       COALESCE(songs >= 2 AND avg_lyrics_score < 0 AND avg_reaction > 0.2, false)
                                  AS negative_lyrics_happy_audience
FROM per_artist;


-- =====================================================================
-- P5. ¿Cuántas semanas aguanta en el top una canción con letra positiva
--     contra una con letra negativa?
--   Mediana y promedio de semanas en el chart por sentimiento de la letra.
--   still_on_chart cuenta las que siguen en el chart (su cuenta puede
--   crecer, así que la mediana real es igual o mayor).
-- =====================================================================
CREATE VIEW v_p5 AS
SELECT lyrics_sentiment,
       count(*)                                                     AS songs,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY weeks_on_chart)  AS median_weeks,
       round(avg(weeks_on_chart), 1)                                AS avg_weeks,
       count(*) FILTER (WHERE still_on_chart)                       AS still_on_chart
FROM v_song_longevity
WHERE lyrics_sentiment IS NOT NULL
GROUP BY lyrics_sentiment;


-- =====================================================================
-- P6. De las canciones que entraron nuevas esta semana, ¿cuáles generan
--     más conversación y con qué emoción?
--   Debut = previous_rank NULL y weeks_on_chart = 1. Trae los comentarios
--   de su semana de debut, la emoción dominante y las vistas que ganó el
--   video esa semana (NULL si no había fotos de estadísticas).
-- =====================================================================
CREATE VIEW v_p6 AS
SELECT w.week_key,
       w.week_end,
       w.recency,
       f.rank,
       f.song_key,
       s.track_name,
       s.main_artist,
       s.artists,
       f.streams,
       COALESCE(r.n_comments, 0)        AS n_comments,
       r.pct_pos,
       r.avg_score,
       r.dominant_emotion,
       emotion_es(r.dominant_emotion)   AS dominant_emotion_es,
       vw.views_gained,
       vw.days_with_stats,
       s.lyrics_sentiment,
       emotion_es(s.lyrics_emotion)     AS lyrics_emotion_es,
       s.youtube_video_id
FROM fact_chart_week f
JOIN v_chart_week w ON w.week_key = f.week_key
JOIN v_song s ON s.song_key = f.song_key
LEFT JOIN song_week_reaction r ON r.week_key = f.week_key AND r.song_key = f.song_key
LEFT JOIN v_video_week vw ON vw.week_key = f.week_key AND vw.song_key = f.song_key
WHERE f.previous_rank IS NULL
  AND f.weeks_on_chart = 1;


-- =====================================================================
-- P7. ¿Cómo reacciona el público a las canciones de mi sello comparado
--     con los otros sellos?
--   Por semana y sello normalizado: canciones en el top 200 y en el top
--   50, porcentaje de los streams de la semana y reacción promedio
--   ponderada por número de comentarios.
-- =====================================================================
CREATE VIEW v_p7 AS
WITH week_total AS (
    SELECT week_key, sum(streams) AS streams FROM fact_chart_week GROUP BY week_key
)
SELECT f.week_key,
       w.week_end,
       w.recency,
       COALESCE(s.label_norm, '(sin sello)')                         AS label_norm,
       COALESCE(max(s.label_name), '(sin sello)')                    AS label_name,
       count(*)                                                     AS songs_top200,
       count(*) FILTER (WHERE f.rank <= 50)                         AS songs_top50,
       min(f.rank)                                                  AS best_rank,
       sum(f.streams)                                               AS streams,
       round(sum(f.streams)::numeric / t.streams, 4)                AS pct_streams,
       COALESCE(sum(r.n_comments), 0)                               AS n_comments,
       round(sum(r.avg_score * r.n_comments) / NULLIF(sum(r.n_comments), 0), 4) AS avg_reaction,
       round(sum(r.pct_pos * r.n_comments) / NULLIF(sum(r.n_comments), 0), 4)   AS pct_pos
FROM fact_chart_week f
JOIN v_chart_week w ON w.week_key = f.week_key
JOIN v_song s ON s.song_key = f.song_key
JOIN week_total t ON t.week_key = f.week_key
LEFT JOIN song_week_reaction r ON r.week_key = f.week_key AND r.song_key = f.song_key
GROUP BY f.week_key, w.week_end, w.recency, COALESCE(s.label_norm, '(sin sello)'), t.streams;
