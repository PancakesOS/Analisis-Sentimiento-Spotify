"""P5. ¿Cuántas semanas aguanta en el top una canción con letra positiva contra una con letra negativa?

Lee v_p5 (mediana y promedio por sentimiento de la letra) y
v_song_longevity (la distribución, canción por canción).
"""

import altair as alt
import streamlit as st

from delivery.dashboard import data, ui

ui.hero("Pregunta 5 · Permanencia en el chart", "¿Aguanta más una letra alegre o una triste?",
        "Semanas en el chart de cada canción (el máximo de weeks_on_chart, que Spotify cuenta "
        "desde su primera aparición) agrupadas por el sentimiento de su letra.",
        "¿Cuántas semanas aguanta en el top una canción con letra positiva contra una con letra "
        "negativa?")

summary = data.query("SELECT * FROM v_p5")
songs = data.query("SELECT * FROM v_song_longevity WHERE lyrics_sentiment IS NOT NULL")
if summary.empty:
    ui.empty("Sin letras analizadas todavía", "lyrics_processor corre cada hora.")
    st.stop()

summary["group"] = summary["lyrics_sentiment"].map(ui.SENTIMENT_ES)
by = {r.lyrics_sentiment: r for r in summary.itertuples()}


def tile(code: str) -> str:
    r = by.get(code)
    if r is None:
        return ui.kpi(f"Letra {ui.SENTIMENT_ES[code].lower()}", None)
    return ui.kpi(f"Letra {ui.SENTIMENT_ES[code].lower()} · mediana", float(r.median_weeks),
                  decimals=1, suffix=" sem.",
                  note=f"{int(r.songs)} canciones · promedio {float(r.avg_weeks):.1f} semanas")


ui.kpi_row([tile("POS"), tile("NEU"), tile("NEG")])
if "POS" in by and "NEG" in by:
    pos, neg = float(by["POS"].median_weeks), float(by["NEG"].median_weeks)
    if abs(pos - neg) < 1:
        verdict = "Las canciones con letra positiva y negativa duran <b>casi lo mismo</b>"
    elif pos > neg:
        verdict = f"Las de letra positiva duran <b>{pos - neg:.1f} semanas más</b> (mediana)"
    else:
        verdict = f"Las de letra negativa duran <b>{neg - pos:.1f} semanas más</b> (mediana)"
    ui.callout(f"<b>Respuesta:</b> {verdict}: {pos:.1f} contra {neg:.1f} semanas.")

ui.section("Distribución de semanas en el chart",
           "Cada punto es una canción; la caja va del cuartil 1 al 3 y la línea blanca es la "
           "mediana. La escala es logarítmica porque unas pocas canciones llevan años en el chart.")
songs["group"] = songs["lyrics_sentiment"].map(ui.SENTIMENT_ES)
songs["status"] = songs["still_on_chart"].map({True: "Sigue en el chart", False: "Ya salió"})
domain = ["Positiva", "Neutra", "Negativa"]
colors = [ui.SENTIMENT_COLORS[d] for d in domain]
box = alt.Chart(songs).mark_boxplot(size=46, extent=1.5, median={"color": "#ffffff", "strokeWidth": 2},
                                    rule={"color": ui.TOKENS["text_2"]},
                                    outliers=False, opacity=0.55).encode(
    x=alt.X("group:N", sort=domain, title="Sentimiento de la letra", axis=alt.Axis(labelAngle=0)),
    y=alt.Y("weeks_on_chart:Q", title="Semanas en el chart",
            scale=alt.Scale(type="log", domainMin=1),
            axis=alt.Axis(values=[1, 2, 5, 10, 20, 50, 100, 200, 500], format="d")),
    color=alt.Color("group:N", legend=None, scale=alt.Scale(domain=domain, range=colors)),
)
dots = alt.Chart(songs).mark_circle(size=28, opacity=0.55).encode(
    x=alt.X("group:N", sort=domain),
    xOffset=alt.XOffset("jitter:Q", scale=alt.Scale(domain=[-1.2, 2.2])),   # en el centro de su grupo
    y="weeks_on_chart:Q",
    color=alt.Color("group:N", legend=None, scale=alt.Scale(domain=domain, range=colors)),
    tooltip=[alt.Tooltip("track_name:N", title="Canción"),
             alt.Tooltip("main_artist:N", title="Artista"),
             alt.Tooltip("weeks_on_chart:Q", title="Semanas en el chart"),
             alt.Tooltip("best_rank:Q", title="Mejor lugar"),
             alt.Tooltip("status:N", title="Estado")],
).transform_calculate(jitter="((datum.song_key * 9301 + 49297) % 233280) / 233280")
ui.chart(box + dots, height=420)

ui.section("Las que más aguantan, por grupo")
cols = st.columns(3, gap="medium")
for col, code in zip(cols, ["POS", "NEU", "NEG"]):
    with col:
        top = songs[songs["lyrics_sentiment"] == code].nlargest(5, "weeks_on_chart")
        st.markdown(f"**Letra {ui.SENTIMENT_ES[code].lower()}**")
        st.dataframe(top[["track_name", "main_artist", "weeks_on_chart"]], hide_index=True,
                     width="stretch",
                     column_config={"track_name": "Canción", "main_artist": "Artista",
                                    "weeks_on_chart": st.column_config.NumberColumn("Semanas",
                                                                                   format="%d")})

still = int(summary["still_on_chart"].sum())
ui.callout(f"<b>Ojo con las que siguen en el chart.</b> {still} canciones con letra analizada "
           "siguen en la semana más reciente: su cuenta todavía puede crecer, así que las medianas "
           "reales son iguales o mayores. Las letras en inglés y las canciones sin letra no se "
           "incluyen.", warn=True)
