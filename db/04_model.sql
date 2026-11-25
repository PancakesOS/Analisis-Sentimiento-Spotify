-- =====================================================================
-- 04_model.sql · Resultados del modelo predictivo
-- =====================================================================
-- delivery/model/train.py entrena una regresión logística que estima si
-- una canción del top 50 sube en el chart la semana siguiente (RFC,
-- sección 7 d) y deja aquí sus métricas y sus predicciones, para que el
-- dashboard las muestre sin abrir el archivo del modelo.
-- =====================================================================


-- ---------------------------------------------------------------------
-- model_run: un entrenamiento.
--   * data_fingerprint: md5 de los datos de entrenamiento y prueba. Con
--     los mismos datos el modelo sale igual (random_state fijo), así que
--     train.py no vuelve a entrenar ni duplica la fila.
--   * Prueba: las 3 semanas más recientes con semana siguiente conocida;
--     entrenamiento: todas las anteriores (validación temporal: el modelo
--     nunca ve el futuro de lo que predice).
--   * baseline_*: la regla "sube si subió la semana pasada".
--   * coefficients: peso de cada variable estandarizada (signo = hacia
--     dónde empuja la probabilidad de subir).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_run (
    run_id              integer      GENERATED ALWAYS AS IDENTITY,
    data_fingerprint    char(32)     NOT NULL,
    trained_at          timestamptz  NOT NULL DEFAULT now(),
    library_version     text         NOT NULL,
    features            text[]       NOT NULL,
    train_from_week     integer      NOT NULL,
    train_to_week       integer      NOT NULL,
    test_from_week      integer      NOT NULL,
    test_to_week        integer      NOT NULL,
    n_train             integer      NOT NULL,
    n_test              integer      NOT NULL,
    train_positive_rate numeric(5,4) NOT NULL,
    test_positive_rate  numeric(5,4) NOT NULL,
    auc                 numeric(5,4),
    baseline_auc        numeric(5,4),
    accuracy            numeric(5,4) NOT NULL,
    baseline_accuracy   numeric(5,4) NOT NULL,
    precision_up        numeric(5,4) NOT NULL,
    recall_up           numeric(5,4) NOT NULL,
    f1_up               numeric(5,4) NOT NULL,
    coefficients        jsonb        NOT NULL,
    model_path          text         NOT NULL,
    CONSTRAINT model_run_pkey             PRIMARY KEY (run_id),
    CONSTRAINT model_run_fingerprint_key  UNIQUE (data_fingerprint),
    CONSTRAINT model_run_n_check          CHECK (n_train > 0 AND n_test > 0)
);


-- ---------------------------------------------------------------------
-- model_prediction: probabilidad de que cada canción del top 50 de la
-- semana más reciente suba la semana siguiente, con el modelo vigente.
-- train.py reemplaza las de la semana en cada corrida (DELETE + INSERT).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_prediction (
    week_key     integer      NOT NULL,
    song_key     integer      NOT NULL,
    run_id       integer      NOT NULL,
    prob_up      numeric(5,4) NOT NULL,
    predicted_at timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT model_prediction_pkey      PRIMARY KEY (week_key, song_key),
    CONSTRAINT model_prediction_week_fkey FOREIGN KEY (week_key) REFERENCES dim_date (date_key),
    CONSTRAINT model_prediction_song_fkey FOREIGN KEY (song_key) REFERENCES dim_song (song_key),
    CONSTRAINT model_prediction_run_fkey  FOREIGN KEY (run_id)   REFERENCES model_run (run_id),
    CONSTRAINT model_prediction_prob_check CHECK (prob_up BETWEEN 0 AND 1)
);
