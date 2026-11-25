"""P1. ¿Qué canciones del top 50 de esta semana tienen la reacción más positiva del público?

Lee v_p1: canciones del top 50 con 20 comentarios o más en la semana,
ordenadas por la proporción de comentarios positivos.
"""

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, state, ui

week_key, week_end = state.week()

ui.hero("Pregunta 1 · Reacción del público", "Las canciones que el público recibe mejor",
        f"Comentarios del video oficial de cada canción del top 50, publicados del "
        f"{ui.week_label(week_end)}. Solo canciones con 20 comentarios o más, para no premiar a "
        "una canción con 3 comentarios.",
        "¿Qué canciones del top 50 de esta semana tienen la reacción más positiva del público?")

df = data.query("""
    SELECT rank, track_name, main_artist, artists, n_comments, pct_pos, pct_neu, pct_neg,
           avg_score, dominant_emotion_es, youtube_video_id
    FROM v_p1 WHERE week_key = %s
    ORDER BY pct_pos DESC, avg_score DESC
""", (week_key,))

if df.empty:
    ui.empty("Sin reacción medible para esta semana",
             "Ninguna canción del top 50 junta 20 comentarios en esta semana todavía. Los "
             "comentarios llegan cada hora; la carga histórica se reparte en varios días.")
    st.stop()

best = df.iloc[0]
ui.kpi_row([
    ui.kpi("Canciones con reacción medible", len(df), suffix=" de 50"),
    ui.kpi("Comentarios analizados", int(df["n_comments"].sum())),
    ui.kpi("Mayor proporción de positivos", float(best["pct_pos"]) * 100, suffix=" %",
           note=f"{best['track_name']} · {best['main_artist']}"),
    ui.kpi("Reacción promedio", float((df["avg_score"] * df["n_comments"]).sum()
                                      / df["n_comments"].sum()), decimals=2, signed=True,
           note="p_pos − p_neg, de −1 a 1"),
])

ui.section("Positivos, neutros y negativos por canción",
           "Cada barra suma 100 % de los comentarios de la canción en la semana. Pasa el cursor "
           "sobre una barra para ver el detalle.")
st.html(ui.legend({"Positivos": ui.TOKENS["pos"], "Neutros": ui.TOKENS["neu"],
                   "Negativos": ui.TOKENS["neg"]}))
df["song"] = "#" + df["rank"].astype(int).astype(str) + "  " + df["track_name"]
long = df.melt(id_vars=["song", "track_name", "main_artist", "n_comments"],
               value_vars=["pct_pos", "pct_neu", "pct_neg"], var_name="kind", value_name="share")
long["kind"] = long["kind"].map({"pct_pos": "Positivos", "pct_neu": "Neutros", "pct_neg": "Negativos"})
long["kind_order"] = long["kind"].map({"Positivos": 0, "Neutros": 1, "Negativos": 2})
order = df["song"].tolist()
bars = alt.Chart(long).mark_bar(height={"band": 0.72}, cornerRadius=0).encode(
    x=alt.X("share:Q", stack="normalize", title=None, axis=alt.Axis(format="%", tickCount=5),
            scale=alt.Scale(domain=[0, 1], nice=False)),
    y=alt.Y("song:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, labelFontSize=12)),
    color=alt.Color("kind:N", legend=None, scale=alt.Scale(
        domain=["Positivos", "Neutros", "Negativos"],
        range=[ui.TOKENS["pos"], ui.TOKENS["neu"], ui.TOKENS["neg"]])),
    order=alt.Order("kind_order:Q"),
    stroke=alt.value(ui.TOKENS["surface"]), strokeWidth=alt.value(2),
    tooltip=[alt.Tooltip("track_name:N", title="Canción"),
             alt.Tooltip("main_artist:N", title="Artista"),
             alt.Tooltip("kind:N", title="Tipo"),
             alt.Tooltip("share:Q", title="Proporción", format=".0%"),
             alt.Tooltip("n_comments:Q", title="Comentarios", format=",")],
)
ui.chart(bars, height=max(220, 26 * len(df)))

ui.section("Detalle", "Ordena la tabla haciendo clic en el encabezado de una columna.")
table = df[["rank", "track_name", "artists", "n_comments", "pct_pos", "pct_neg", "avg_score",
            "dominant_emotion_es", "youtube_video_id"]].copy()
table["youtube_video_id"] = "https://www.youtube.com/watch?v=" + table["youtube_video_id"].fillna("")
st.dataframe(
    table, hide_index=True, width="stretch",
    column_config={
        "rank": st.column_config.NumberColumn("Lugar", format="%d", width="small"),
        "track_name": st.column_config.TextColumn("Canción"),
        "artists": st.column_config.TextColumn("Artistas"),
        "n_comments": st.column_config.NumberColumn("Comentarios", format="localized"),
        "pct_pos": st.column_config.ProgressColumn("Positivos", format="percent", min_value=0,
                                                   max_value=1),
        "pct_neg": st.column_config.ProgressColumn("Negativos", format="percent", min_value=0,
                                                   max_value=1),
        "avg_score": st.column_config.NumberColumn("Reacción", format="%+.2f",
                                                   help="Promedio de p_pos − p_neg"),
        "dominant_emotion_es": st.column_config.TextColumn("Emoción dominante"),
        "youtube_video_id": st.column_config.LinkColumn("Video", display_text="Abrir"),
    })

ui.callout("<b>Cómo leerlo.</b> Un comentario cuenta como positivo, neutro o negativo según el "
           "modelo de sentimiento (RoBERTuito, entrenado con tweets en español, que entiende jerga "
           "y emojis). La emoción dominante es la más frecuente sin contar los comentarios sin "
           "emoción clara. El público que comenta en YouTube no es necesariamente el que escucha "
           "en Spotify.")

if not pd.isna(best["avg_score"]):
    ui.footer(f"Semana del {ui.week_label(week_end)} · {len(df)} canciones · "
              f"{int(df['n_comments'].sum()):,} comentarios")
