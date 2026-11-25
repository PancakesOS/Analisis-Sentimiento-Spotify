"""train: regresión logística que estima si una canción del top 50 sube la semana siguiente.

Responde la segunda parte de la pregunta P2 y el objetivo 7 del RFC.
Corre cada hora con cron, pero solo vuelve a entrenar cuando cambian los
datos (una semana nueva del chart o comentarios nuevos de semanas viejas);
las predicciones de la semana más reciente sí se recalculan cada vez.

Datos: v_model_dataset, una fila por canción del top 50 y semana, solo
con MIN_COMMENTS comentarios o más en la semana (sin comentarios no hay
reacción que medir) y con la semana siguiente ya cargada.

Variable objetivo: went_up, la canción quedó en un lugar mejor la semana
siguiente (si salió del chart, cuenta como que no subió).

Variables (todas se estandarizan antes de entrenar):
  avg_score         reacción promedio del público (p_pos - p_neg);
  log_comments      log(1 + comentarios de la semana): volumen de conversación;
  comments_growth   log(1 + comentarios) - log(1 + comentarios de la semana
                    anterior): si la conversación crece o se apaga;
  rank              lugar de la semana;
  log_weeks         log(semanas en el chart): canciones nuevas contra veteranas;
  rank_change       lugares que subió esta semana (0 si es debut o reingreso);
  is_new            1 si es debut o reingreso;
  lyrics_score      sentimiento de la letra (0 si no tiene letra analizada);
  has_lyrics        1 si tiene letra analizada.

El RFC proponía usar también el crecimiento de vistas del video, pero la
foto diaria de estadísticas empezó el 20 de noviembre de 2026: las semanas
de entrenamiento no tienen ese dato. En su lugar está comments_growth.

Regularización: con pocas semanas de comentarios hay pocos ejemplos y
nueve variables, así que la fuerza de la regularización (C) se elige con
validación cruzada dentro del entrenamiento, separando por semana
(GroupKFold): las filas de una misma semana nunca quedan a los dos lados.
La prueba no se usa para elegir nada.

Evaluación (validación temporal): la prueba son las 3 semanas más
recientes con semana siguiente conocida y el entrenamiento todas las
anteriores, así el modelo nunca ve el futuro de lo que predice. Se compara
contra el baseline del RFC: "sube si subió la semana pasada". La métrica
principal es el AUC (meta: 0.60 o más y mejor que el baseline).

Reproducible: random_state fijo y los mismos datos dan el mismo modelo.
El modelo se guarda con joblib en un archivo temporal que luego se
renombra; si algo falla, se queda el anterior. Las métricas van a
model_run y las predicciones a model_prediction.
"""

import hashlib
import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
import psycopg
import sklearn
from sklearn.linear_model import LogisticRegressionCV
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common.jobs import STATE_DIR, pg_params, setup_logging, single_run

JOB = "train"
MODEL_DIR = STATE_DIR / "models"
MIN_COMMENTS = 5
TEST_WEEKS = 3
MIN_TRAIN_ROWS = 40
RANDOM_STATE = 0
CV_FOLDS = 5
C_GRID = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]
FEATURES = ["avg_score", "log_comments", "comments_growth", "rank", "log_weeks",
            "rank_change", "is_new", "lyrics_score", "has_lyrics"]

DATASET_SQL = """
    SELECT week_key, song_key, rank, previous_rank, weeks_on_chart, went_up, went_up_last_week,
           n_comments, n_comments_prev_week, avg_score, lyrics_score
    FROM v_model_dataset
    WHERE n_comments >= %s
    ORDER BY week_key, song_key
"""

SAVE_RUN_SQL = """
    INSERT INTO model_run (data_fingerprint, library_version, features, train_from_week,
                           train_to_week, test_from_week, test_to_week, n_train, n_test,
                           train_positive_rate, test_positive_rate, auc, baseline_auc, accuracy,
                           baseline_accuracy, precision_up, recall_up, f1_up, coefficients,
                           model_path)
    VALUES (%(fingerprint)s, %(library_version)s, %(features)s, %(train_from_week)s,
            %(train_to_week)s, %(test_from_week)s, %(test_to_week)s, %(n_train)s, %(n_test)s,
            %(train_positive_rate)s, %(test_positive_rate)s, %(auc)s, %(baseline_auc)s,
            %(accuracy)s, %(baseline_accuracy)s, %(precision_up)s, %(recall_up)s, %(f1_up)s,
            %(coefficients)s, %(model_path)s)
    RETURNING run_id
"""


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula las variables del modelo a partir de las columnas de v_model_dataset."""
    out = pd.DataFrame(index=df.index)
    n = df["n_comments"].astype(float)
    n_prev = df["n_comments_prev_week"].astype(float)
    out["avg_score"] = df["avg_score"].astype(float).fillna(0.0)
    out["log_comments"] = np.log1p(n)
    out["comments_growth"] = np.log1p(n) - np.log1p(n_prev)
    out["rank"] = df["rank"].astype(float)
    out["log_weeks"] = np.log(df["weeks_on_chart"].astype(float))
    previous = df["previous_rank"].astype(float)
    out["rank_change"] = (previous - out["rank"]).fillna(0.0)
    out["is_new"] = previous.isna().astype(float)
    lyrics = df["lyrics_score"].astype(float)
    out["lyrics_score"] = lyrics.fillna(0.0)
    out["has_lyrics"] = lyrics.notna().astype(float)
    return out[FEATURES]


def make_classifier(train: pd.DataFrame, y_train):
    """Estandariza y ajusta la regresión logística; C se elige con validación cruzada por semana."""
    groups = train["week_key"].to_numpy()
    folds = min(CV_FOLDS, len(set(groups)))
    splits = list(GroupKFold(n_splits=folds).split(train, y_train, groups))
    # Un pliegue con una sola clase no permite calcular el AUC: se descarta.
    splits = [(a, b) for a, b in splits if len(set(y_train[b])) == 2] or folds
    model = make_pipeline(StandardScaler(), LogisticRegressionCV(
        Cs=C_GRID, cv=splits, scoring="roc_auc", l1_ratios=(0.0,), use_legacy_attributes=True,
        max_iter=2000, random_state=RANDOM_STATE))   # l1_ratios=(0,): penalización L2 pura
    model.fit(build_features(train), y_train)
    return model


def fingerprint(df: pd.DataFrame) -> str:
    """md5 de los datos etiquetados y de la configuración del modelo: si no cambian, el modelo
    tampoco. Incluir la configuración hace que un cambio en el código vuelva a entrenar."""
    spec = f"{FEATURES}|{C_GRID}|{CV_FOLDS}|{TEST_WEEKS}|{MIN_COMMENTS}|{sklearn.__version__}"
    return hashlib.md5((spec + df.to_csv(index=False)).encode("utf-8")).hexdigest()


def safe_auc(y, score) -> float | None:
    return float(roc_auc_score(y, score)) if len(set(y)) == 2 else None


def evaluate(model, x_test, y_test, baseline) -> dict:
    prob = model.predict_proba(x_test)[:, 1]
    pred = (prob >= 0.5).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, pred, average="binary", pos_label=1, zero_division=0)
    return {"auc": safe_auc(y_test, prob), "baseline_auc": safe_auc(y_test, baseline),
            "accuracy": float(accuracy_score(y_test, pred)),
            "baseline_accuracy": float(accuracy_score(y_test, baseline)),
            "precision_up": float(precision), "recall_up": float(recall), "f1_up": float(f1)}


def save_model(model, name: str):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = MODEL_DIR / name
    tmp = target.with_suffix(".tmp")
    joblib.dump({"model": model, "features": FEATURES}, tmp)
    os.replace(tmp, target)
    return target


def r4(value):
    return None if value is None else round(value, 4)


def run(log, pg: dict) -> int:
    started = time.monotonic()
    errors, outcome, predictions = 0, "sin_cambios", 0
    try:
        with psycopg.connect(**pg, connect_timeout=10, autocommit=True) as conn:
            cur = conn.execute(DATASET_SQL, (MIN_COMMENTS,))
            df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
            labeled = df[df["went_up"].notna()].reset_index(drop=True)
            weeks = sorted(labeled["week_key"].unique())
            log.info("inicio; filas=%d etiquetadas=%d semanas_etiquetadas=%d min_comentarios=%d",
                     len(df), len(labeled), len(weeks), MIN_COMMENTS)

            run_id = None
            if len(weeks) <= TEST_WEEKS:
                outcome = "pocos_datos"
                log.warning("solo hay %d semanas con reacción y semana siguiente; hacen falta más "
                            "de %d para separar la prueba", len(weeks), TEST_WEEKS)
            else:
                test_weeks = weeks[-TEST_WEEKS:]
                train = labeled[~labeled["week_key"].isin(test_weeks)]
                test = labeled[labeled["week_key"].isin(test_weeks)]
                y_train = train["went_up"].astype(int).to_numpy()
                y_test = test["went_up"].astype(int).to_numpy()
                if len(train) < MIN_TRAIN_ROWS or len(set(y_train)) < 2:
                    outcome = "pocos_datos"
                    log.warning("entrenamiento con %d filas y clases %s; hacen falta %d filas y "
                                "las dos clases", len(train), sorted(set(y_train)), MIN_TRAIN_ROWS)
                else:
                    fp = fingerprint(labeled)
                    row = conn.execute("SELECT run_id FROM model_run WHERE data_fingerprint = %s",
                                       (fp,)).fetchone()
                    if row:
                        run_id = row[0]
                        log.info("los datos no cambiaron (run_id=%d); no se vuelve a entrenar",
                                 run_id)
                    else:
                        model = make_classifier(train, y_train)
                        metrics = evaluate(model, build_features(test), y_test,
                                           test["went_up_last_week"].astype(int).to_numpy())
                        path = save_model(model, f"model_{fp}.joblib")
                        coefs = dict(zip(FEATURES, model[-1].coef_[0].round(4).tolist()))
                        coefs["intercepto"] = round(float(model[-1].intercept_[0]), 4)
                        coefs["C"] = float(model[-1].C_[0])
                        with conn.transaction():
                            run_id = conn.execute(SAVE_RUN_SQL, {
                                "fingerprint": fp,
                                "library_version": f"scikit-learn {sklearn.__version__}",
                                "features": FEATURES,
                                "train_from_week": int(train["week_key"].min()),
                                "train_to_week": int(train["week_key"].max()),
                                "test_from_week": int(min(test_weeks)),
                                "test_to_week": int(max(test_weeks)),
                                "n_train": len(train), "n_test": len(test),
                                "train_positive_rate": r4(float(y_train.mean())),
                                "test_positive_rate": r4(float(y_test.mean())),
                                **{k: r4(v) for k, v in metrics.items()},
                                "coefficients": json.dumps(coefs),
                                "model_path": str(path),
                            }).fetchone()[0]
                        outcome = "entrenado"
                        log.info("modelo nuevo run_id=%d: auc=%s baseline_auc=%s accuracy=%.3f "
                                 "baseline_accuracy=%.3f n_train=%d n_test=%d C=%s", run_id,
                                 r4(metrics["auc"]), r4(metrics["baseline_auc"]),
                                 metrics["accuracy"], metrics["baseline_accuracy"],
                                 len(train), len(test), coefs["C"])

            if run_id is not None:
                predictions = predict_latest(conn, df, run_id)
    except psycopg.OperationalError as e:
        errors += 1
        log.error("No se pudo conectar a PostgreSQL; se reintenta en la siguiente corrida: %s", e)
    except Exception:
        errors += 1
        log.exception("Error inesperado; se queda el modelo anterior")
    finally:
        log.info("fin; duracion=%.1fs resultado=%s predicciones=%d errores=%d",
                 time.monotonic() - started, outcome, predictions, errors)
    return errors


def predict_latest(conn, df: pd.DataFrame, run_id: int) -> int:
    """Probabilidad de subir de las canciones de la semana más reciente, con el modelo run_id."""
    path = conn.execute("SELECT model_path FROM model_run WHERE run_id = %s", (run_id,)).fetchone()[0]
    model = joblib.load(path)["model"]
    latest_week = conn.execute("SELECT max(week_key) FROM fact_chart_week").fetchone()[0]
    latest = df[df["week_key"] == latest_week].reset_index(drop=True)
    rows = []
    if not latest.empty:
        prob = model.predict_proba(build_features(latest))[:, 1]
        rows = [(int(latest_week), int(s), run_id, round(float(p), 4))
                for s, p in zip(latest["song_key"], prob)]
    with conn.transaction():
        conn.execute("DELETE FROM model_prediction WHERE week_key = %s", (latest_week,))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO model_prediction (week_key, song_key, run_id, prob_up) "
                            "VALUES (%s, %s, %s, %s)", rows)
    return len(rows)


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
