-- =====================================================================
-- 05_validation.sql · Validación manual (criterios de éxito del RFC)
-- =====================================================================
-- Dos criterios de la sección 10 del RFC necesitan a una persona:
--
--   * Calidad del sentimiento: coincidencia del modelo contra 100
--     comentarios etiquetados a mano (meta: 70 % o más).
--   * Video oficial correcto: revisión de 20 canciones del top 50
--     elegidas al azar (meta: 90 % o más).
--
-- Las etiquetas se capturan con scripts/label_sentiment.py y
-- scripts/review_videos.py --revisar, y la página "Calidad de los datos"
-- del dashboard muestra el resultado.
-- =====================================================================

SET search_path TO control, silver, gold, public;  -- las tablas de este archivo van a control



-- ---------------------------------------------------------------------
-- sentiment_label: un comentario etiquetado a mano.
--   * No guarda el texto: solo el id, la etiqueta de la persona y la del
--     modelo en ese momento. Así la etiqueta puede quedarse aunque
--     retention borre el comentario a los 30 días (por eso no hay llave
--     foránea a fact_comment).
--   * SKIP: comentario que no se pudo etiquetar (otro idioma, solo una
--     mención, no se entiende). No cuenta para la coincidencia.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sentiment_label (
    comment_id      text         NOT NULL,
    human_sentiment text         NOT NULL,
    model_sentiment text         NOT NULL,
    model_version   text         NOT NULL,
    labeled_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT sentiment_label_pkey        PRIMARY KEY (comment_id),
    CONSTRAINT sentiment_label_human_check CHECK (human_sentiment IN ('POS', 'NEU', 'NEG', 'SKIP')),
    CONSTRAINT sentiment_label_model_check CHECK (model_sentiment IN ('POS', 'NEU', 'NEG'))
);


-- ---------------------------------------------------------------------
-- video_check: la revisión manual del video elegido para una canción.
--   * youtube_video_id: el video que se revisó (el que tenía la canción
--     en ese momento). Si era incorrecto y se corrigió, dim_song ya
--     tiene el nuevo, pero aquí queda el revisado: la métrica mide qué
--     tan bien eligió video_finder, no el estado después de corregir.
--   * video_match: cómo se había elegido (auto, review o manual).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_check (
    song_key         integer      NOT NULL,
    youtube_video_id text         NOT NULL,
    video_match      text         NOT NULL,
    is_correct       boolean      NOT NULL,
    checked_at       timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT video_check_pkey      PRIMARY KEY (song_key, youtube_video_id),
    CONSTRAINT video_check_song_fkey FOREIGN KEY (song_key) REFERENCES dim_song (song_key)
);
