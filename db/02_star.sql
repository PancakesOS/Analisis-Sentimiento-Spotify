-- =====================================================================
-- 02_star.sql · Modelo estrella
-- =====================================================================
-- Dimensiones, tabla puente y tablas de hechos de la sección 7 c del RFC.
--
--   Dimensiones: dim_date, dim_artist, dim_song, song_artist (puente)
--   Hechos:      fact_chart_week    (canción × semana del chart)
--                fact_comment       (un comentario principal)
--                fact_video_daily   (snapshot periódico video × día)
--                song_week_reaction (agregado canción × semana)
--
--   * Todas las dimensiones son SCD tipo 1: un cambio sobrescribe el
--     valor y actualiza updated_at. La historia del chart y de las
--     vistas vive en los hechos (una fila por semana o por día).
--   * Las semanas se identifican con week_key: el date_key (AAAAMMDD)
--     del jueves en que termina la semana del chart (viernes a jueves).
--   * Ningún hecho guarda datos del autor de un comentario.
--   * Ninguna FK lleva ON DELETE CASCADE.
-- =====================================================================


-- ---------------------------------------------------------------------
-- dim_date: un renglón por día, de 2005 (año en que nació YouTube: un
-- comentario no puede ser más viejo) a 2035. Se llena aquí mismo, así
-- que ningún trabajo tiene que crear fechas.
--   * chart_week_end: el jueves de la semana del chart que contiene el
--     día. Para un viernes es el jueves siguiente; para un jueves, él
--     mismo.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_date (
    date_key       integer  NOT NULL,       -- AAAAMMDD
    date           date     NOT NULL,
    chart_week_end date     NOT NULL,
    month          smallint NOT NULL,
    year           smallint NOT NULL,
    CONSTRAINT dim_date_pkey        PRIMARY KEY (date_key),
    CONSTRAINT dim_date_date_key    UNIQUE (date),
    CONSTRAINT dim_date_week_check  CHECK (chart_week_end BETWEEN date AND date + 6),
    CONSTRAINT dim_date_month_check CHECK (month BETWEEN 1 AND 12)
);

-- isodow: lunes = 1 … jueves = 4 … domingo = 7.
-- (11 - isodow) % 7 = días que faltan para el siguiente jueves (0 si es jueves).
INSERT INTO dim_date (date_key, date, chart_week_end, month, year)
SELECT to_char(d, 'YYYYMMDD')::integer,
       d,
       d + (11 - extract(isodow FROM d)::integer) % 7,
       extract(month FROM d)::smallint,
       extract(year FROM d)::smallint
FROM generate_series(date '2005-01-01', date '2035-12-31', interval '1 day') AS g(ts),
     LATERAL (SELECT ts::date AS d) AS x
ON CONFLICT (date_key) DO NOTHING;


-- ---------------------------------------------------------------------
-- dim_artist: un artista. Lo identifica su nombre normalizado
-- (minúsculas y sin acentos), porque el CSV solo trae nombres.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_artist (
    artist_key       integer     GENERATED ALWAYS AS IDENTITY,
    artist_name      text        NOT NULL,  -- como aparece en el chart más reciente
    artist_name_norm text        NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dim_artist_pkey     PRIMARY KEY (artist_key),
    CONSTRAINT dim_artist_norm_key UNIQUE (artist_name_norm)
);


-- ---------------------------------------------------------------------
-- dim_song: una canción, identificada por su URI de Spotify.
--   * Del chart:   track_name, label (columna source del CSV),
--                  main_artist_key (primer artista), first_seen_week.
--   * Normalizado: track_name_norm y label_norm, para emparejar con
--                  LRCLIB y YouTube y para agrupar sellos que el CSV
--                  escribe distinto ("Josa Records (Neton Vega)").
--   * De YouTube:  youtube_video_id y video_match (auto = el canal
--                  coincide con el artista o el sello; review = se tomó
--                  el primer resultado y falta revisarlo; manual =
--                  corregido a mano). video_checked_at evita repetir
--                  una búsqueda de 100 unidades.
--   * De LRCLIB:   lyrics_status (NULL = todavía no se busca),
--                  lrclib_id, lyrics_checked_at y el sentimiento de la
--                  letra con la versión del modelo que lo calculó.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_song (
    song_key          integer      GENERATED ALWAYS AS IDENTITY,
    spotify_uri       text         NOT NULL,
    track_name        text         NOT NULL,
    track_name_norm   text         NOT NULL,
    main_artist_key   integer      NOT NULL,
    label             text,
    label_norm        text,
    youtube_video_id  text,
    video_match       text,
    video_checked_at  timestamptz,
    lyrics_status     text,
    lyrics_checked_at timestamptz,
    lrclib_id         integer,
    lyrics_language   text,
    lyrics_sentiment  text,
    lyrics_p_pos      numeric(5,4),
    lyrics_p_neu      numeric(5,4),
    lyrics_p_neg      numeric(5,4),
    lyrics_emotion    text,
    model_version     text,
    first_seen_week   integer      NOT NULL,
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT dim_song_pkey               PRIMARY KEY (song_key),
    CONSTRAINT dim_song_spotify_uri_key    UNIQUE (spotify_uri),
    CONSTRAINT dim_song_main_artist_fkey   FOREIGN KEY (main_artist_key)
        REFERENCES dim_artist (artist_key),
    CONSTRAINT dim_song_first_seen_fkey    FOREIGN KEY (first_seen_week)
        REFERENCES dim_date (date_key),
    CONSTRAINT dim_song_video_match_check  CHECK (video_match IN ('auto', 'review', 'manual')),
    CONSTRAINT dim_song_lyrics_status_check
        CHECK (lyrics_status IN ('found', 'not_found', 'instrumental')),
    CONSTRAINT dim_song_lyrics_sentiment_check CHECK (lyrics_sentiment IN ('POS', 'NEU', 'NEG')),
    CONSTRAINT dim_song_lyrics_p_check CHECK (
        lyrics_p_pos BETWEEN 0 AND 1 AND lyrics_p_neu BETWEEN 0 AND 1 AND lyrics_p_neg BETWEEN 0 AND 1
    )
);


-- ---------------------------------------------------------------------
-- song_uri: todas las URI de Spotify de cada canción.
-- Spotify a veces cambia la URI de una canción que ya está en el chart
-- (y a veces regresa a la anterior), pero mantiene su continuidad: la
-- fila con la URI nueva trae en previous_rank el lugar que ocupaba la
-- anterior, o, si reingresa al chart, sigue la cuenta de weeks_on_chart.
-- En las 54 semanas del 2025-11-06 al 2026-11-12, 663 canciones tienen
-- 716 URI. Sin esta tabla, una canción quedaría partida en varias.
--   * dim_song.spotify_uri es la URI con la que se dio de alta.
--   * link_reason (ver transform/song_identity.py; siempre con el mismo
--     título normalizado y el mismo artista principal):
--       original      -> es la URI de alta;
--       previous_rank -> su previous_rank lleva a la misma canción en la
--                        semana anterior;
--       reentry       -> reingresa con weeks_on_chart una semana mayor que
--                        la última aparición de la misma canción.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS song_uri (
    spotify_uri text        NOT NULL,
    song_key    integer     NOT NULL,
    link_reason text        NOT NULL,
    linked_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT song_uri_pkey         PRIMARY KEY (spotify_uri),
    CONSTRAINT song_uri_song_fkey    FOREIGN KEY (song_key) REFERENCES dim_song (song_key),
    CONSTRAINT song_uri_reason_check CHECK (link_reason IN ('original', 'previous_rank', 'reentry'))
);

CREATE INDEX IF NOT EXISTS song_uri_song_key_idx ON song_uri (song_key);


-- ---------------------------------------------------------------------
-- song_artist: tabla puente canción–artista. 83 de las 200 canciones de
-- una semana tienen varios artistas; sin el puente se perderían las
-- colaboraciones (pregunta P4). artist_order = 1 es el artista principal.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS song_artist (
    song_key     integer  NOT NULL,
    artist_key   integer  NOT NULL,
    artist_order smallint NOT NULL,
    CONSTRAINT song_artist_pkey        PRIMARY KEY (song_key, artist_key),
    CONSTRAINT song_artist_order_key   UNIQUE (song_key, artist_order),
    CONSTRAINT song_artist_order_check CHECK (artist_order >= 1),
    CONSTRAINT song_artist_song_fkey   FOREIGN KEY (song_key)   REFERENCES dim_song (song_key),
    CONSTRAINT song_artist_artist_fkey FOREIGN KEY (artist_key) REFERENCES dim_artist (artist_key)
);

CREATE INDEX IF NOT EXISTS song_artist_artist_key_idx ON song_artist (artist_key);


-- ---------------------------------------------------------------------
-- fact_chart_week. Grano: una canción en una semana del chart.
--   * streams es aditiva entre canciones de la misma semana.
--   * rank, peak_rank, previous_rank y weeks_on_chart no son aditivas.
--   * previous_rank es NULL cuando el CSV trae -1 (no estaba la semana
--     anterior: un debut si weeks_on_chart = 1, un reingreso si es > 1).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_chart_week (
    week_key       integer  NOT NULL,
    song_key       integer  NOT NULL,
    rank           smallint NOT NULL,
    peak_rank      smallint NOT NULL,
    previous_rank  smallint,
    weeks_on_chart smallint NOT NULL,
    streams        bigint   NOT NULL,
    CONSTRAINT fact_chart_week_pkey           PRIMARY KEY (week_key, song_key),
    CONSTRAINT fact_chart_week_rank_key       UNIQUE (week_key, rank),
    CONSTRAINT fact_chart_week_week_fkey      FOREIGN KEY (week_key) REFERENCES dim_date (date_key),
    CONSTRAINT fact_chart_week_song_fkey      FOREIGN KEY (song_key) REFERENCES dim_song (song_key),
    CONSTRAINT fact_chart_week_rank_check     CHECK (rank BETWEEN 1 AND 200),
    CONSTRAINT fact_chart_week_peak_check     CHECK (peak_rank BETWEEN 1 AND 200),
    CONSTRAINT fact_chart_week_previous_check CHECK (previous_rank BETWEEN 1 AND 200),
    CONSTRAINT fact_chart_week_weeks_check    CHECK (weeks_on_chart >= 1),
    CONSTRAINT fact_chart_week_streams_check  CHECK (streams >= 0)
);

CREATE INDEX IF NOT EXISTS fact_chart_week_song_key_idx ON fact_chart_week (song_key);


-- ---------------------------------------------------------------------
-- fact_comment. Grano: un comentario principal del video oficial.
--   * date_key es el día de publicación en hora de México; su semana
--     del chart sale de dim_date.chart_week_end.
--   * score = p_pos - p_neg, de -1 a 1 (columna calculada).
--   * like_count y reply_count son los del momento de la ingesta.
--   * discarded = true cuando el comentario es de un video que resultó
--     no ser el oficial: deja de contar, pero no se borra hasta que
--     cumpla los 30 días.
--   * Retención: retention.py borra las filas con ingested_at de más de
--     30 días (política de YouTube). Por eso hay un índice por fecha de
--     ingesta.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_comment (
    comment_id    text         NOT NULL,
    song_key      integer      NOT NULL,
    video_id      text         NOT NULL,
    date_key      integer      NOT NULL,
    published_at  timestamptz  NOT NULL,
    ingested_at   timestamptz  NOT NULL DEFAULT now(),
    text_original text         NOT NULL,
    like_count    integer      NOT NULL DEFAULT 0,
    reply_count   integer      NOT NULL DEFAULT 0,
    p_pos         numeric(5,4) NOT NULL,
    p_neu         numeric(5,4) NOT NULL,
    p_neg         numeric(5,4) NOT NULL,
    score         numeric(5,4) GENERATED ALWAYS AS (p_pos - p_neg) STORED,
    sentiment     text         NOT NULL,
    emotion       text,
    model_version text         NOT NULL,
    discarded     boolean      NOT NULL DEFAULT false,
    CONSTRAINT fact_comment_pkey            PRIMARY KEY (comment_id),
    CONSTRAINT fact_comment_song_fkey       FOREIGN KEY (song_key) REFERENCES dim_song (song_key),
    CONSTRAINT fact_comment_date_fkey       FOREIGN KEY (date_key) REFERENCES dim_date (date_key),
    CONSTRAINT fact_comment_sentiment_check CHECK (sentiment IN ('POS', 'NEU', 'NEG')),
    CONSTRAINT fact_comment_counts_check    CHECK (like_count >= 0 AND reply_count >= 0),
    CONSTRAINT fact_comment_p_check CHECK (
        p_pos BETWEEN 0 AND 1 AND p_neu BETWEEN 0 AND 1 AND p_neg BETWEEN 0 AND 1
    )
);

CREATE INDEX IF NOT EXISTS fact_comment_song_date_idx   ON fact_comment (song_key, date_key);
CREATE INDEX IF NOT EXISTS fact_comment_ingested_at_idx ON fact_comment (ingested_at);


-- ---------------------------------------------------------------------
-- fact_video_daily. Grano: un video en un día (snapshot periódico).
--   * Las medidas son acumuladas al momento de la foto: las vistas de
--     un día son la diferencia contra la foto del día anterior.
--   * like_count y comment_count pueden ser NULL si el video oculta los
--     likes o tiene los comentarios desactivados.
--   * Se conserva sin límite de 30 días: las estadísticas no son texto
--     de comentarios.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_video_daily (
    date_key      integer     NOT NULL,
    song_key      integer     NOT NULL,
    video_id      text        NOT NULL,
    view_count    bigint      NOT NULL,
    like_count    bigint,
    comment_count bigint,
    captured_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fact_video_daily_pkey         PRIMARY KEY (date_key, song_key),
    CONSTRAINT fact_video_daily_date_fkey    FOREIGN KEY (date_key) REFERENCES dim_date (date_key),
    CONSTRAINT fact_video_daily_song_fkey    FOREIGN KEY (song_key) REFERENCES dim_song (song_key),
    CONSTRAINT fact_video_daily_counts_check CHECK (
        view_count >= 0 AND (like_count IS NULL OR like_count >= 0)
        AND (comment_count IS NULL OR comment_count >= 0)
    )
);

CREATE INDEX IF NOT EXISTS fact_video_daily_song_key_idx ON fact_video_daily (song_key);


-- ---------------------------------------------------------------------
-- song_week_reaction. Grano: una canción en una semana del chart
-- (agregado de fact_comment sin los descartados).
--   * pct_pos, pct_neu y pct_neg son proporciones de 0 a 1 que suman 1.
--   * avg_score es el promedio de score, de -1 a 1.
--   * aggregate_week la recalcula desde cero (DELETE + INSERT por
--     semana), así que correrla dos veces da lo mismo. Es lo único que
--     queda de los comentarios después de los 30 días.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS song_week_reaction (
    week_key         integer      NOT NULL,
    song_key         integer      NOT NULL,
    n_comments       integer      NOT NULL,
    pct_pos          numeric(5,4) NOT NULL,
    pct_neu          numeric(5,4) NOT NULL,
    pct_neg          numeric(5,4) NOT NULL,
    avg_score        numeric(5,4) NOT NULL,
    dominant_emotion text,
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT song_week_reaction_pkey       PRIMARY KEY (week_key, song_key),
    CONSTRAINT song_week_reaction_week_fkey  FOREIGN KEY (week_key) REFERENCES dim_date (date_key),
    CONSTRAINT song_week_reaction_song_fkey  FOREIGN KEY (song_key) REFERENCES dim_song (song_key),
    CONSTRAINT song_week_reaction_n_check    CHECK (n_comments > 0),
    CONSTRAINT song_week_reaction_pct_check  CHECK (
        pct_pos BETWEEN 0 AND 1 AND pct_neu BETWEEN 0 AND 1 AND pct_neg BETWEEN 0 AND 1
    ),
    CONSTRAINT song_week_reaction_score_check CHECK (avg_score BETWEEN -1 AND 1)
);
