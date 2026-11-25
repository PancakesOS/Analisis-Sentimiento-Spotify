"""P4. ¿Qué artistas tienen letras negativas pero un público feliz?

Lee v_p4: por artista (principal o invitado, gracias a la tabla puente),
el sentimiento promedio de sus letras y la reacción promedio del público
en las últimas 12 semanas.
"""

import altair as alt
import streamlit as st

from delivery.dashboard import data, ui

ui.hero("Pregunta 4 · Letra contra público", "Letras oscuras, público feliz",
        "Cada punto es un artista con canciones en el chart de las últimas 12 semanas, como "
        "principal o como invitado. A la izquierda, letras más negativas; arriba, un público que "
        "reacciona mejor. La esquina superior izquierda es la respuesta.",
        "¿Qué artistas tienen letras negativas pero un público feliz?")

df = data.query("""
    SELECT * FROM v_p4
    WHERE avg_lyrics_score IS NOT NULL AND avg_reaction IS NOT NULL AND n_comments >= 20
    ORDER BY n_comments DESC
""")
if df.empty:
    ui.empty("Todavía no hay artistas con letra y reacción",
             "Hace falta que sus canciones tengan letra analizada y 20 comentarios o más.")
    st.stop()

answer = df[df["negative_lyrics_happy_audience"]].sort_values("avg_reaction", ascending=False)
ui.kpi_row([
    ui.kpi("Artistas con letra y reacción", len(df), note="20 comentarios o más en el periodo"),
    ui.kpi("Letra negativa, público feliz", len(answer),
           note="2+ canciones · letra < 0 · reacción > 0.2"),
    ui.kpi("Letra promedio de los artistas", float(df["avg_lyrics_score"].mean()), decimals=2,
           signed=True),
    ui.kpi("Reacción promedio de los artistas", float(df["avg_reaction"].mean()), decimals=2,
           signed=True),
])

ui.section("Sentimiento de la letra contra reacción del público",
           "El tamaño del punto es el número de comentarios. Las líneas punteadas marcan los "
           "umbrales de la pregunta: letra = 0 y reacción = 0.2.")
df["group"] = df["negative_lyrics_happy_audience"].map(
    {True: "Letra negativa, público feliz", False: "Resto de los artistas"})
st.html(ui.legend({"Letra negativa, público feliz": ui.TOKENS["accent"],
                   "Resto de los artistas": ui.TOKENS["neu"]}))
hover = alt.selection_point(fields=["artist_name"], on="pointerover", empty=False)
points = alt.Chart(df).mark_circle(stroke=ui.TOKENS["bg"], strokeWidth=1.5).encode(
    x=alt.X("avg_lyrics_score:Q", title="Sentimiento promedio de sus letras (−1 a 1)",
            scale=alt.Scale(zero=False, nice=True)),
    y=alt.Y("avg_reaction:Q", title="Reacción promedio del público (−1 a 1)",
            scale=alt.Scale(zero=False, nice=True)),
    size=alt.Size("n_comments:Q", legend=None, scale=alt.Scale(range=[60, 900])),
    color=alt.Color("group:N", legend=None, scale=alt.Scale(
        domain=["Letra negativa, público feliz", "Resto de los artistas"],
        range=[ui.TOKENS["accent"], ui.TOKENS["neu"]])),
    opacity=alt.condition(hover, alt.value(1), alt.value(0.8)),
    tooltip=[alt.Tooltip("artist_name:N", title="Artista"),
             alt.Tooltip("songs:Q", title="Canciones"),
             alt.Tooltip("avg_lyrics_score:Q", title="Letra promedio", format="+.2f"),
             alt.Tooltip("avg_reaction:Q", title="Reacción promedio", format="+.2f"),
             alt.Tooltip("n_comments:Q", title="Comentarios", format=",")],
).add_params(hover)
labels = alt.Chart(df[df["negative_lyrics_happy_audience"]]).mark_text(
    align="left", dx=10, dy=-6, fontSize=12, fontWeight=600, color=ui.TOKENS["text"]).encode(
    x="avg_lyrics_score:Q", y="avg_reaction:Q", text="artist_name:N")
vrule = alt.Chart().mark_rule(strokeDash=[4, 4], color=ui.TOKENS["axis"]).encode(x=alt.datum(0))
hrule = alt.Chart().mark_rule(strokeDash=[4, 4], color=ui.TOKENS["axis"]).encode(y=alt.datum(0.2))
ui.chart(vrule + hrule + points + labels, height=440)

ui.section("La respuesta", "Artistas con 2 canciones o más, letras negativas en promedio y una "
           "reacción del público mayor que 0.2.")
if answer.empty:
    ui.callout("Con los datos actuales <b>ningún artista</b> cumple las tres condiciones. Revisa la "
               "gráfica: los más cercanos están en la esquina superior izquierda.")
else:
    st.dataframe(
        answer[["artist_name", "songs", "avg_lyrics_score", "avg_reaction", "n_comments"]],
        hide_index=True, width="stretch",
        column_config={
            "artist_name": st.column_config.TextColumn("Artista"),
            "songs": st.column_config.NumberColumn("Canciones", format="%d"),
            "avg_lyrics_score": st.column_config.NumberColumn("Letra promedio", format="%+.2f"),
            "avg_reaction": st.column_config.NumberColumn("Reacción promedio", format="%+.2f"),
            "n_comments": st.column_config.NumberColumn("Comentarios", format="localized"),
        })

with st.expander("Ver todos los artistas"):
    st.dataframe(
        df[["artist_name", "songs", "songs_with_lyrics", "songs_with_reaction",
            "avg_lyrics_score", "avg_reaction", "n_comments"]],
        hide_index=True, width="stretch",
        column_config={
            "artist_name": "Artista", "songs": "Canciones",
            "songs_with_lyrics": "Con letra", "songs_with_reaction": "Con reacción",
            "avg_lyrics_score": st.column_config.NumberColumn("Letra promedio", format="%+.2f"),
            "avg_reaction": st.column_config.NumberColumn("Reacción promedio", format="%+.2f"),
            "n_comments": st.column_config.NumberColumn("Comentarios", format="localized"),
        })

ui.callout("<b>Por qué importa la tabla puente.</b> Un artista cuenta todas sus canciones, también "
           "las colaboraciones donde aparece como invitado; si solo se tomara al artista "
           "principal, se perderían.")
