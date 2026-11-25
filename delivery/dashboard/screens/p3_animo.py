"""P3. ¿El top de México está más triste o más alegre que hace un mes?

Lee v_p3: el índice de ánimo de cada semana (sentimiento de las letras del
top 50 ponderado por streams), su cambio contra 4 semanas antes y qué
parte de los streams tiene letra alegre o triste.
"""

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, state, ui

week_key, week_end = state.week()

ui.hero("Pregunta 3 · Ánimo del top", "¿Más triste o más alegre que hace un mes?",
        "El índice de ánimo promedia el sentimiento de la letra de cada canción del top 50 "
        "(p_pos − p_neg, de −1 a 1), dándole más peso a la que tiene más streams. No depende de "
        "los comentarios, así que cubre toda la historia cargada del chart.",
        "¿El top de México está más triste o más alegre que hace un mes?")

df = data.query("SELECT * FROM v_p3 WHERE mood_index IS NOT NULL ORDER BY week_end")
if df.empty:
    ui.empty("Sin letras analizadas todavía",
             "lyrics_fetcher busca las letras en LRCLIB y lyrics_processor calcula su sentimiento "
             "cada hora.")
    st.stop()

row = df[df["week_key"] == week_key]
row = row.iloc[0] if not row.empty else df.iloc[-1]
change = None if pd.isna(row["change_4w"]) else float(row["change_4w"])
if change is None:
    verdict = "No hay semana de hace 4 semanas para comparar."
elif abs(change) < 0.02:
    verdict = "Prácticamente <b>igual</b> que hace un mes."
elif change > 0:
    verdict = f"<b>Más alegre</b> que hace un mes ({change:+.2f})."
else:
    verdict = f"<b>Más triste</b> que hace un mes ({change:+.2f})."

ui.kpi_row([
    ui.kpi("Índice de ánimo", float(row["mood_index"]), decimals=2, signed=True,
           delta=change, delta_suffix=" en 4 semanas",
           note=f"semana del {ui.week_label(row['week_end'])}"),
    ui.kpi("Streams con letra alegre", float(row["pct_streams_joy"]) * 100, suffix=" %",
           note="emoción dominante: alegría"),
    ui.kpi("Streams con letra triste", float(row["pct_streams_sadness"]) * 100, suffix=" %",
           note="emoción dominante: tristeza"),
    ui.kpi("Cobertura de letras", float(row["streams_coverage"]) * 100, suffix=" %",
           note=f"{int(row['songs_with_lyrics'])} de {int(row['songs'])} canciones, por streams"),
])
ui.callout(f"<b>Respuesta:</b> {verdict} Un índice mayor que 0 significa que, ponderadas por "
           "streams, pesan más las letras positivas que las negativas.")

ui.section("Índice de ánimo semana a semana",
           "La línea punteada marca el 0 (letras positivas y negativas en equilibrio). El punto "
           "resaltado es la semana elegida en la barra lateral.")
hover = alt.selection_point(fields=["week_end"], nearest=True, on="pointerover", empty=False)
base = alt.Chart(df).encode(x=alt.X("week_end:T", title=None,
                                    axis=alt.Axis(format="%b %Y", labelAngle=0, tickCount=8)))
line = base.mark_line(color=ui.TOKENS["accent"], strokeWidth=2.5, interpolate="monotone").encode(
    y=alt.Y("mood_index:Q", title="Índice de ánimo"))
area = base.mark_area(opacity=0.18, color=ui.TOKENS["accent"], interpolate="monotone").encode(
    y="mood_index:Q", y2=alt.datum(0))
points = base.mark_point(size=90, color=ui.TOKENS["accent"]).encode(
    y="mood_index:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    tooltip=[alt.Tooltip("week_end:T", title="Semana que termina", format="%d %b %Y"),
             alt.Tooltip("mood_index:Q", title="Índice", format="+.3f"),
             alt.Tooltip("change_4w:Q", title="Cambio en 4 semanas", format="+.3f"),
             alt.Tooltip("streams_coverage:Q", title="Cobertura de letras", format=".0%")],
).add_params(hover)
selected = alt.Chart(df[df["week_key"] == row["week_key"]]).mark_point(
    size=160, filled=True, color="#ffffff", stroke=ui.TOKENS["accent"], strokeWidth=3).encode(
    x="week_end:T", y="mood_index:Q")
zero = alt.Chart().mark_rule(strokeDash=[4, 4], color=ui.TOKENS["axis"]).encode(y=alt.datum(0))
ui.chart(area + line + zero + points + selected, height=340)

ui.section("Qué parte de los streams del top 50 tiene letra alegre y cuál triste",
           "Ayuda a explicar los cambios del índice: cuando sube la línea de tristeza, el índice "
           "suele bajar.")
shares = df.melt(id_vars=["week_end"], value_vars=["pct_streams_joy", "pct_streams_sadness"],
                 var_name="kind", value_name="share")
shares["kind"] = shares["kind"].map({"pct_streams_joy": "alegría", "pct_streams_sadness": "tristeza"})
st.html(ui.legend({"Letra alegre": ui.EMOTION_COLORS["alegría"],
                   "Letra triste": ui.EMOTION_COLORS["tristeza"]}))
hover2 = alt.selection_point(fields=["week_end"], nearest=True, on="pointerover", empty=False)
lines = alt.Chart(shares).mark_line(strokeWidth=2.5, interpolate="monotone").encode(
    x=alt.X("week_end:T", title=None, axis=alt.Axis(format="%b %Y", labelAngle=0, tickCount=8)),
    y=alt.Y("share:Q", title="Parte de los streams", axis=alt.Axis(format="%")),
    color=alt.Color("kind:N", legend=None, scale=alt.Scale(
        domain=["alegría", "tristeza"],
        range=[ui.EMOTION_COLORS["alegría"], ui.EMOTION_COLORS["tristeza"]])),
)
dots = lines.mark_point(size=70).encode(
    opacity=alt.condition(hover2, alt.value(1), alt.value(0)),
    tooltip=[alt.Tooltip("week_end:T", title="Semana que termina", format="%d %b %Y"),
             alt.Tooltip("kind:N", title="Letra"),
             alt.Tooltip("share:Q", title="Streams", format=".1%")],
).add_params(hover2)
ui.chart(lines + dots, height=260)

with st.expander("Ver la tabla semana por semana"):
    st.dataframe(
        df.sort_values("week_end", ascending=False)[
            ["week_end", "mood_index", "change_4w", "pct_streams_pos", "pct_streams_neg",
             "streams_coverage"]],
        hide_index=True, width="stretch",
        column_config={
            "week_end": st.column_config.DateColumn("Semana que termina", format="D MMM YYYY"),
            "mood_index": st.column_config.NumberColumn("Índice", format="%+.3f"),
            "change_4w": st.column_config.NumberColumn("Cambio en 4 semanas", format="%+.3f"),
            "pct_streams_pos": st.column_config.NumberColumn("Streams con letra positiva",
                                                             format="percent"),
            "pct_streams_neg": st.column_config.NumberColumn("Streams con letra negativa",
                                                             format="percent"),
            "streams_coverage": st.column_config.NumberColumn("Cobertura", format="percent"),
        })

ui.callout("<b>Limitaciones.</b> El sentimiento de una letra es ruidoso: jerga, doble sentido y "
           "spanglish. Se analiza por estrofa y se promedia, y solo se usan letras en español (el "
           "modelo es para español). Las canciones sin letra en LRCLIB no cuentan en el índice; "
           "la cobertura dice qué parte de los streams sí.")
