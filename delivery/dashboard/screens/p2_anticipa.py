"""P2. Las canciones que la gente recibe bien en YouTube, ¿suben en el chart la semana siguiente?

Lee v_p2 (quintiles de reacción y qué porcentaje subió) y el último
model_run (la evaluación del modelo contra el baseline).
"""

import altair as alt
import streamlit as st

from delivery.dashboard import data, ui

ui.hero("Pregunta 2 · ¿La reacción anticipa subidas?", "Buena reacción hoy, ¿mejor lugar mañana?",
        "Se juntan todas las semanas con reacción medible (20 comentarios o más) y se dividen las "
        "canciones del top 50 en cinco grupos del mismo tamaño según su proporción de comentarios "
        "positivos. Luego se mide cuántas de cada grupo subieron la semana siguiente.",
        "Las canciones que la gente recibe bien en YouTube, ¿suben en el chart la semana siguiente?")

q = data.query("SELECT * FROM v_p2 ORDER BY quintile")
base_rate = data.scalar("""
    SELECT avg(went_up::int) FROM v_model_dataset WHERE went_up IS NOT NULL AND n_comments >= 20
""")

if q.empty:
    ui.empty("Todavía no hay semanas suficientes",
             "Hacen falta canciones con 20 comentarios o más en una semana cuya semana siguiente "
             "ya esté cargada. Se llena conforme avanza la carga histórica de comentarios.")
else:
    low, high = q.iloc[0], q.iloc[-1]
    diff = (float(high["pct_went_up"]) - float(low["pct_went_up"])) * 100
    ui.kpi_row([
        ui.kpi("Canciones-semana analizadas", int(q["songs"].sum()),
               note=f"{int(q['weeks'].max())} semanas distintas"),
        ui.kpi("Subieron · reacción más positiva", float(high["pct_went_up"]) * 100, suffix=" %",
               note="quintil 5"),
        ui.kpi("Subieron · reacción más negativa", float(low["pct_went_up"]) * 100, suffix=" %",
               note="quintil 1"),
        ui.kpi("Diferencia entre extremos", diff, decimals=0, suffix=" pts", signed=True,
               note="puntos porcentuales"),
    ])

    ui.section("Porcentaje que subió la semana siguiente, por quintil de reacción",
               "La línea punteada es el promedio de todas las canciones. Si la reacción anticipa "
               "subidas, las barras deberían crecer de izquierda a derecha.")
    q["group"] = q["quintile"].map({1: "Q1 · más negativa", 2: "Q2", 3: "Q3", 4: "Q4",
                                    5: "Q5 · más positiva"})
    bars = alt.Chart(q).mark_bar(size=46).encode(
        x=alt.X("group:N", sort=None, title="Quintil de comentarios positivos",
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("pct_went_up:Q", title="Subieron la semana siguiente",
                axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, max(0.6, float(
                    q["pct_went_up"].max()) * 1.2)])),
        color=alt.Color("quintile:O", legend=None, scale=alt.Scale(
            range=["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6"])),
        tooltip=[alt.Tooltip("group:N", title="Grupo"),
                 alt.Tooltip("min_pct_pos:Q", title="Positivos desde", format=".0%"),
                 alt.Tooltip("max_pct_pos:Q", title="Positivos hasta", format=".0%"),
                 alt.Tooltip("songs:Q", title="Canciones-semana"),
                 alt.Tooltip("pct_went_up:Q", title="Subieron", format=".0%")],
    )
    labels = bars.mark_text(dy=-10, fontSize=13, fontWeight=600, color=ui.TOKENS["text"]).encode(
        text=alt.Text("pct_went_up:Q", format=".0%"))
    layers = bars + labels
    if base_rate is not None:
        rule = alt.Chart().mark_rule(strokeDash=[5, 4], color=ui.TOKENS["text_2"]).encode(
            y=alt.datum(float(base_rate)))
        layers = layers + rule
    ui.chart(layers, height=340)

ui.section("Lo que dice el modelo", "Una regresión logística que estima si una canción del top 50 "
           "sube la semana siguiente, evaluada en las 3 semanas más recientes que no vio al "
           "entrenar.")
run = data.query("SELECT * FROM model_run ORDER BY run_id DESC LIMIT 1")
if run.empty:
    ui.empty("El modelo todavía no se entrena",
             "Se entrena solo en cuanto hay más de 3 semanas con reacción medible y semana "
             "siguiente conocida.")
else:
    r = run.iloc[0]
    ui.kpi_row([
        ui.kpi("AUC del modelo", None if r["auc"] != r["auc"] else float(r["auc"]), decimals=2,
               note="0.5 = azar · meta del RFC: 0.60 o más"),
        ui.kpi("AUC del baseline", None if r["baseline_auc"] != r["baseline_auc"]
               else float(r["baseline_auc"]), decimals=2, note="«sube si subió la semana pasada»"),
        ui.kpi("Exactitud del modelo", float(r["accuracy"]) * 100, suffix=" %",
               note=f"baseline: {float(r['baseline_accuracy']):.0%}"),
    ])
    st.page_link("screens/modelo.py", label="Ver el modelo completo y sus predicciones",
                 icon=":material/arrow_forward:")

ui.callout("<b>Correlación no es causa.</b> Una canción puede recibir buenos comentarios y subir "
           "por otra razón (un video nuevo, una playlist, un concierto). Esta página dice si la "
           "reacción sirve como señal temprana, no que la cause.")
