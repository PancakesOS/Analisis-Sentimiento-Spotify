-- =====================================================================
-- 01_control.sql · Tablas de control del flujo
-- =====================================================================
-- No forman parte del modelo estrella: registran qué ya se hizo, para
-- que cada trabajo se pueda correr dos veces sin duplicar datos ni
-- gastar cuota de YouTube de más (RFC, secciones 7 c y 7 d).
--
--   * etl_loaded_file: cada CSV del chart que vio chart_loader.
--   * etl_quota_usage: unidades de cuota de YouTube gastadas por día.
--   * video_watermark: hasta qué comentario se leyó cada video.
--
-- Se ejecuta solo cuando el volumen de PostgreSQL está vacío. Usa
-- IF NOT EXISTS para poder correrlo también a mano con psql. Ninguna FK
-- lleva ON DELETE CASCADE: los borrados se hacen de forma explícita.
-- =====================================================================


-- ---------------------------------------------------------------------
-- etl_loaded_file: un renglón por cada archivo distinto (por contenido)
-- que apareció en data/landing/charts/.
--   * La llave es el sha256: si el mismo archivo se vuelve a encontrar,
--     aunque tenga otro nombre, chart_loader lo omite.
--   * status:
--       loaded   -> es el archivo vigente de su semana;
--       replaced -> se cargó, pero después llegó otro archivo de la
--                   misma semana y lo reemplazó;
--       rejected -> no pasó la validación (el motivo va en detail) y no
--                   se cargó nada de él. Queda registrado para no
--                   volver a intentarlo cada 15 minutos.
--   * Solo puede haber un archivo 'loaded' por semana (índice parcial).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl_loaded_file (
    file_sha256    char(64)    NOT NULL,
    file_name      text        NOT NULL,
    chart_week_end date,                    -- jueves en que termina la semana del chart
    row_count      integer,                 -- filas cargadas en fact_chart_week
    raw_path       text,                    -- copia en la zona cruda, relativa a data/raw/
    status         text        NOT NULL,
    detail         text,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT etl_loaded_file_pkey         PRIMARY KEY (file_sha256),
    CONSTRAINT etl_loaded_file_status_check CHECK (status IN ('loaded', 'replaced', 'rejected')),
    CONSTRAINT etl_loaded_file_loaded_check CHECK (
        status = 'rejected'
        OR (chart_week_end IS NOT NULL AND row_count IS NOT NULL AND raw_path IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS etl_loaded_file_one_loaded_per_week
    ON etl_loaded_file (chart_week_end)
    WHERE status = 'loaded';


-- ---------------------------------------------------------------------
-- etl_quota_usage: unidades de cuota de YouTube gastadas por día y por
-- método. Solo escribe aquí youtube_client.call(), que se niega a
-- llamar a la API si el día ya pasó de 9,000 unidades.
--   * quota_date es el día en hora del Pacífico (America/Los_Angeles),
--     porque la cuota de YouTube se reinicia a medianoche de esa zona.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl_quota_usage (
    quota_date date        NOT NULL,
    method     text        NOT NULL,        -- search.list, commentThreads.list, videos.list
    units      integer     NOT NULL DEFAULT 0,
    calls      integer     NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT etl_quota_usage_pkey            PRIMARY KEY (quota_date, method),
    CONSTRAINT etl_quota_usage_units_positive  CHECK (units >= 0),
    CONSTRAINT etl_quota_usage_calls_positive  CHECK (calls >= 0)
);


-- ---------------------------------------------------------------------
-- video_watermark: hasta dónde leyó comments_producer cada video.
--   * last_published_at: fecha del comentario más reciente que ya se
--     publicó en Kafka. El poller pide comentarios del más nuevo al más
--     viejo y se detiene al llegar a esta marca. Solo avanza después de
--     kafka.flush(): entrega al menos una vez.
--   * Carga histórica: la primera vez se leen también los comentarios
--     viejos, página por página, hasta el inicio de la historia del
--     chart. backfill_page_token guarda la página siguiente, para seguir
--     al otro día si se acaba la cuota; backfill_done marca el final.
--   * last_polled_at decide cuándo toca la siguiente lectura (cada hora
--     para el top 50 vigente, una vez al día para los demás videos).
--   * disabled_reason: el video tiene los comentarios desactivados o ya
--     no existe; se deja de leer.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_watermark (
    video_id            text        NOT NULL,
    last_published_at   timestamptz,
    backfill_page_token text,
    backfill_done       boolean     NOT NULL DEFAULT false,
    last_polled_at      timestamptz,
    disabled_reason     text,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT video_watermark_pkey PRIMARY KEY (video_id)
);
