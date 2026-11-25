"""Modelo predictivo: evaluación contra el baseline, qué variables pesan y predicciones de la semana.

Lee model_run (métricas y coeficientes del último entrenamiento) y
model_prediction (probabilidad de subir de cada canción del top 50 de la
semana más reciente).
"""

import json

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, ui

FEATURE_NAMES = {
    "avg_score": "Reacción del público",
    "log_comments": "Volumen de comentarios",
    "comments_growth": "Crecimiento de comentarios",
    "rank": "Lugar actual",
    "log_weeks": "Semanas en el chart",
    "rank_change": "Lugares que subió esta semana",
    "is_new": "Debut o reingreso",
    "lyrics_score": "Sentimiento de la letra",
    "has_lyrics": "Tiene letra analizada",
}

ui.hero("Análisis · Modelo predictivo", "¿Qué canciones van a subir la próxima semana?",
        "Una regresión logística estima la probabilidad de que cada canción del top 50 quede en un "
        "mejor lugar la semana siguiente, con la reacción del público, el volumen y crecimiento de "
        "comentarios, su lugar, su antigüedad y el sentimiento de su letra.")

runs = data.query("SELECT * FROM model_run ORDER BY run_id DESC")
if runs.empty:
    ui.empty("El modelo todavía no se entrena",
             "delivery/model/train.py corre cada hora y entrena en cuanto hay más de 3 semanas con "
             "reacción medible y semana siguiente conocida.")
    st.stop()

r = runs.iloc[0]
auc = None if pd.isna(r["auc"]) else float(r["auc"])
base_auc = None if pd.isna(r["baseline_auc"]) else float(r["baseline_auc"])
ui.kpi_row([
    ui.kpi("AUC del modelo", auc, decimals=2,
           delta=None if auc is None or base_auc is None else auc - base_auc,
           delta_suffix=" contra el baseline", note="0.5 = azar · 1 = perfecto"),
    ui.kpi("AUC del baseline", base_auc, decimals=2, note="«sube si subió la semana pasada»"),
    ui.kpi("Exactitud", float(r["accuracy"]) * 100, suffix=" %",
           note=f"baseline {float(r['baseline_accuracy']):.0%} · umbral 50 %"),
    ui.kpi("Precisión al decir «sube»", float(r["precision_up"]) * 100, suffix=" %",
           note=f"encuentra {float(r['recall_up']):.0%} de las que suben"),
])

goal = auc is not None and auc >= 0.60 and (base_auc is None or auc > base_auc)
ui.callout(
    ("<b>Cumple la meta del RFC</b> (AUC de 0.60 o más y mejor que el baseline)." if goal else
     "<b>Todavía no cumple la meta del RFC</b> (AUC de 0.60 o más y mejor que el baseline). Con más "
     "semanas de comentarios el modelo tiene más ejemplos para aprender.")
    + f" Entrenado con <b>{int(r['n_train'])}</b> canciones-semana "
    f"({ui.short_date(pd.to_datetime(str(r['train_from_week'])).date())} a "
    f"{ui.short_date(pd.to_datetime(str(r['train_to_week'])).date())}) y probado con "
    f"<b>{int(r['n_test'])}</b> de las 3 semanas siguientes, que el modelo no vio al entrenar.",
    warn=not goal)

left, right = st.columns([1, 1], gap="large")
with left:
    ui.section("Qué empuja la probabilidad de subir",
               "Peso de cada variable estandarizada: a la derecha (azul) aumenta la probabilidad, a "
               "la izquierda (rojo) la reduce.")
    coefs = json.loads(r["coefficients"]) if isinstance(r["coefficients"], str) else r["coefficients"]
    cdf = pd.DataFrame([{"feature": FEATURE_NAMES.get(k, k), "coef": v}
                        for k, v in coefs.items() if k not in ("intercepto", "C")])
    cdf["direction"] = cdf["coef"].map(lambda v: "Aumenta" if v >= 0 else "Reduce")
    bars = alt.Chart(cdf).mark_bar(height={"band": 0.65}).encode(
        x=alt.X("coef:Q", title="Coeficiente (variables estandarizadas)"),
        y=alt.Y("feature:N", sort=alt.EncodingSortField("coef", order="descending"), title=None,
                axis=alt.Axis(labelLimit=220)),
        color=alt.Color("direction:N", legend=None, scale=alt.Scale(
            domain=["Aumenta", "Reduce"], range=[ui.TOKENS["pos"], ui.TOKENS["neg"]])),
        tooltip=[alt.Tooltip("feature:N", title="Variable"),
                 alt.Tooltip("coef:Q", title="Coeficiente", format="+.3f")],
    )
    zero = alt.Chart().mark_rule(color=ui.TOKENS["axis"]).encode(x=alt.datum(0))
    ui.chart(zero + bars, height=320)

with right:
    ui.section("Predicciones para la semana más reciente",
               "Probabilidad de que cada canción quede en un mejor lugar la próxima semana.")
    preds = data.query("""
        SELECT f.rank, s.track_name, s.main_artist, p.prob_up
        FROM model_prediction p
        JOIN fact_chart_week f ON f.week_key = p.week_key AND f.song_key = p.song_key
        JOIN v_song s ON s.song_key = p.song_key
        WHERE p.week_key = (SELECT max(week_key) FROM model_prediction)
        ORDER BY p.prob_up DESC
    """)
    if preds.empty:
        ui.empty("Sin predicciones", "Ninguna canción de la semana más reciente tiene 5 comentarios "
                 "o más todavía.")
    else:
        st.dataframe(
            preds, hide_index=True, width="stretch", height=330,
            column_config={
                "rank": st.column_config.NumberColumn("Lugar", format="%d", width="small"),
                "track_name": st.column_config.TextColumn("Canción", width="medium"),
                "main_artist": st.column_config.TextColumn("Artista", width="small"),
                "prob_up": st.column_config.ProgressColumn("Prob. de subir", format="percent",
                                                           min_value=0, max_value=1, width="small"),
            })

if len(runs) > 1:
    ui.section("Historial de entrenamientos", "Se vuelve a entrenar solo cuando cambian los datos.")
    hist = runs[["run_id", "trained_at", "n_train", "n_test", "auc", "baseline_auc"]].copy()
    hist["trained_at"] = pd.to_datetime(hist["trained_at"]).dt.tz_convert("America/Mexico_City")
    st.dataframe(hist, hide_index=True, width="stretch", column_config={
        "run_id": "Corrida", "trained_at": st.column_config.DatetimeColumn(
            "Entrenado", format="D MMM YYYY, HH:mm"),
        "n_train": "Entrenamiento", "n_test": "Prueba",
        "auc": st.column_config.NumberColumn("AUC", format="%.3f"),
        "baseline_auc": st.column_config.NumberColumn("AUC baseline", format="%.3f")})

ui.callout("<b>Cómo se evalúa.</b> Validación temporal: la prueba son siempre las 3 semanas más "
           "recientes con resultado conocido, y el entrenamiento todas las anteriores; así el modelo "
           "nunca ve el futuro de lo que predice. El AUC mide qué tan bien ordena: la probabilidad "
           "de que una canción que sí subió reciba una probabilidad mayor que una que no subió. "
           "El modelo usa random_state fijo: con los mismos datos da el mismo resultado.")
