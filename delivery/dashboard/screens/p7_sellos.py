"""P7. ¿Cómo reacciona el público a las canciones de mi sello comparado con los otros sellos?

Lee v_p7 (por semana y sello normalizado) y junta la semana elegida o las
últimas 4 semanas. "Mi sello" se elige en la barra lateral.
"""

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, state, ui

week_key, week_end = state.week()
label_norm, label_name = state.my_label()

ui.hero("Pregunta 7 · Mi sello contra los demás", f"{label_name or 'Mi sello'} frente al resto",
        "Peso de cada sello en el top 200 (porcentaje de los streams) y reacción del público a sus "
        "canciones (promedio de p_pos − p_neg ponderado por número de comentarios). Los sellos se "
        "agrupan aunque el CSV los escriba distinto.",
        "¿Cómo reacciona el público a las canciones de mi sello comparado con los otros sellos?")

period = st.segmented_control("Periodo", ["Semana elegida", "Últimas 4 semanas"],
                              default="Semana elegida", key="p7_period")
if period == "Últimas 4 semanas":
    weeks = data.query("SELECT week_key FROM v_chart_week WHERE week_end BETWEEN %s::date - 21 "
                       "AND %s::date", (week_end, week_end))["week_key"].tolist()
else:
    weeks = [week_key]

raw = data.query("""
    SELECT p.label_norm, p.label_name, p.songs_top200, p.songs_top50, p.best_rank, p.streams,
           p.n_comments, p.avg_reaction, p.week_key
    FROM v_p7 p WHERE p.week_key = ANY(%s)
""", (weeks,))
total_streams = float(raw["streams"].sum())
raw["weighted"] = raw["avg_reaction"].fillna(0) * raw["n_comments"]
df = raw.groupby("label_norm", as_index=False).agg(
    label_name=("label_name", "first"), songs_top200=("songs_top200", "sum"),
    songs_top50=("songs_top50", "sum"), best_rank=("best_rank", "min"), streams=("streams", "sum"),
    n_comments=("n_comments", "sum"), weighted=("weighted", "sum"))
df["pct_streams"] = df["streams"] / total_streams
df["avg_reaction"] = df["weighted"] / df["n_comments"].where(df["n_comments"] > 0)
df = df.sort_values("streams", ascending=False).reset_index(drop=True)
df["position"] = df.index + 1
df["is_mine"] = df["label_norm"] == label_norm
chart_reaction = float(df["weighted"].sum() / df["n_comments"].sum()) if df["n_comments"].sum() else None

mine = df[df["is_mine"]]
if mine.empty:
    ui.callout(f"<b>{ui.esc(label_name)}</b> no tiene canciones en el top 200 en este periodo.",
               warn=True)
else:
    m = mine.iloc[0]
    weeks_txt = "en la semana" if len(weeks) == 1 else f"en {len(weeks)} semanas"
    ui.kpi_row([
        ui.kpi("Parte de los streams", float(m["pct_streams"]) * 100, decimals=1, suffix=" %",
               note=f"lugar {int(m['position'])} de {len(df)} sellos"),
        ui.kpi("Canciones en el top 200", int(m["songs_top200"]),
               note=f"{weeks_txt} · {int(m['songs_top50'])} en el top 50"),
        ui.kpi("Reacción del público", None if pd.isna(m["avg_reaction"]) else float(m["avg_reaction"]),
               decimals=2, signed=True,
               delta=None if pd.isna(m["avg_reaction"]) or chart_reaction is None
               else float(m["avg_reaction"]) - chart_reaction, delta_suffix=" contra el chart",
               note=f"{int(m['n_comments']):,} comentarios"),
        ui.kpi("Reacción promedio del chart", chart_reaction, decimals=2, signed=True,
               note="todos los sellos, ponderada por comentarios"),
    ])

left, right = st.columns([1, 1], gap="large")
with left:
    ui.section("Los sellos con más streams", "Los 12 primeros; tu sello se resalta y siempre "
               "aparece.")
    top = df.head(12)
    if not mine.empty and not top["is_mine"].any():
        top = pd.concat([top, mine])
    top = top.assign(group=top["is_mine"].map({True: "Mi sello", False: "Otros sellos"}))
    bars = alt.Chart(top).mark_bar(height={"band": 0.7}).encode(
        x=alt.X("pct_streams:Q", title="Parte de los streams del top 200",
                axis=alt.Axis(format="%")),
        y=alt.Y("label_name:N", sort=alt.EncodingSortField("streams", order="descending"),
                title=None, axis=alt.Axis(labelLimit=200)),
        color=alt.Color("group:N", legend=None, scale=alt.Scale(
            domain=["Mi sello", "Otros sellos"], range=[ui.TOKENS["accent"], "#4a4b52"])),
        tooltip=[alt.Tooltip("label_name:N", title="Sello"),
                 alt.Tooltip("pct_streams:Q", title="Streams", format=".1%"),
                 alt.Tooltip("songs_top200:Q", title="Canciones (top 200)"),
                 alt.Tooltip("best_rank:Q", title="Mejor lugar")],
    )
    ui.chart(bars, height=34 * len(top) + 40)

with right:
    ui.section("Peso contra reacción", "Sellos con 20 comentarios o más en el periodo. Arriba, "
               "el público reacciona mejor; a la derecha, más streams.")
    scatter_df = df[df["n_comments"] >= 20].assign(
        group=lambda d: d["is_mine"].map({True: "Mi sello", False: "Otros sellos"}))
    if scatter_df.empty:
        ui.empty("Sin reacción suficiente", "Ningún sello junta 20 comentarios en el periodo.")
    else:
        hover = alt.selection_point(fields=["label_norm"], on="pointerover", empty=False)
        pts = alt.Chart(scatter_df).mark_circle(stroke=ui.TOKENS["bg"], strokeWidth=1.5).encode(
            x=alt.X("pct_streams:Q", title="Parte de los streams", axis=alt.Axis(format="%")),
            y=alt.Y("avg_reaction:Q", title="Reacción del público", scale=alt.Scale(zero=False)),
            size=alt.Size("n_comments:Q", legend=None, scale=alt.Scale(range=[60, 800])),
            color=alt.Color("group:N", legend=None, scale=alt.Scale(
                domain=["Mi sello", "Otros sellos"], range=[ui.TOKENS["accent"], "#6b6a66"])),
            opacity=alt.condition(hover, alt.value(1), alt.value(0.85)),
            tooltip=[alt.Tooltip("label_name:N", title="Sello"),
                     alt.Tooltip("pct_streams:Q", title="Streams", format=".1%"),
                     alt.Tooltip("avg_reaction:Q", title="Reacción", format="+.2f"),
                     alt.Tooltip("n_comments:Q", title="Comentarios", format=",")],
        ).add_params(hover)
        named = scatter_df[scatter_df["is_mine"] | (scatter_df["position"] <= 5)]
        txt = alt.Chart(named).mark_text(align="left", dx=10, dy=-6, fontSize=11,
                                         color=ui.TOKENS["text_2"]).encode(
            x="pct_streams:Q", y="avg_reaction:Q", text="label_name:N")
        layers = pts + txt
        if chart_reaction is not None:
            layers = alt.Chart().mark_rule(strokeDash=[4, 4], color=ui.TOKENS["axis"]).encode(
                y=alt.datum(chart_reaction)) + layers
        ui.chart(layers, height=34 * min(len(top), 13) + 40)

with st.expander("Ver todos los sellos"):
    st.dataframe(
        df[["position", "label_name", "songs_top200", "songs_top50", "best_rank", "pct_streams",
            "n_comments", "avg_reaction"]],
        hide_index=True, width="stretch",
        column_config={
            "position": st.column_config.NumberColumn("#", format="%d", width="small"),
            "label_name": "Sello", "songs_top200": "Canciones top 200",
            "songs_top50": "Canciones top 50", "best_rank": "Mejor lugar",
            "pct_streams": st.column_config.ProgressColumn("Streams", format="percent",
                                                           min_value=0,
                                                           max_value=float(df["pct_streams"].max())),
            "n_comments": st.column_config.NumberColumn("Comentarios", format="localized"),
            "avg_reaction": st.column_config.NumberColumn("Reacción", format="%+.2f"),
        })

ui.callout("<b>Cómo leerlo.</b> Con 4 semanas, las canciones se cuentan una vez por semana en que "
           "aparecen. La reacción solo existe para canciones del top 50 con video, así que un sello "
           "con canciones solo del 51 al 200 aparece sin reacción.")
