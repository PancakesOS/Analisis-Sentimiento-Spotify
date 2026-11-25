"""P6. De las canciones que entraron nuevas esta semana, ¿cuáles generan más conversación y con qué emoción?

Lee v_p6: los debuts de la semana (previous_rank vacío y weeks_on_chart =
1), con sus comentarios, su emoción dominante y las vistas que ganó el
video.
"""

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, state, ui

week_key, week_end = state.week()

ui.hero("Pregunta 6 · Debuts de la semana", "Las nuevas que ya dan de qué hablar",
        f"Canciones que entraron por primera vez al top 200 la semana del {ui.week_label(week_end)}, "
        "ordenadas por cuántos comentarios juntó su video oficial esa semana.",
        "De las canciones que entraron nuevas esta semana, ¿cuáles generan más conversación y con "
        "qué emoción?")

df = data.query("""
    SELECT rank, track_name, main_artist, artists, streams, n_comments, pct_pos, avg_score,
           dominant_emotion_es, views_gained, days_with_stats, lyrics_sentiment,
           lyrics_emotion_es, youtube_video_id
    FROM v_p6 WHERE week_key = %s
    ORDER BY n_comments DESC, rank
""", (week_key,))
if df.empty:
    ui.empty("No hubo debuts esta semana", "Ninguna canción entró por primera vez al top 200.")
    st.stop()

with_talk = df[df["n_comments"] > 0]
ui.kpi_row([
    ui.kpi("Debuts en el top 200", len(df), note=f"{int((df['rank'] <= 50).sum())} en el top 50"),
    ui.kpi("Con comentarios", len(with_talk),
           note="solo se siguen los videos del top 50"),
    ui.kpi("Comentarios de los debuts", int(df["n_comments"].sum())),
    ui.kpi("Mejor debut", int(df["rank"].min()), prefix="#",
           note=df.loc[df["rank"].idxmin(), "track_name"]),
])

if not with_talk.empty:
    ui.section("Conversación por debut y su emoción dominante",
               "Cada barra es un debut con comentarios; el color es la emoción que más aparece en "
               "sus comentarios.")
    chart_df = with_talk.copy()
    chart_df["emotion"] = chart_df["dominant_emotion_es"].fillna("sin emoción clara")
    chart_df["song"] = "#" + chart_df["rank"].astype(int).astype(str) + "  " + chart_df["track_name"]
    present = [e for e in ui.EMOTION_ORDER if e in set(chart_df["emotion"])]
    st.html(ui.legend({e: ui.EMOTION_COLORS[e] for e in present}))
    bars = alt.Chart(chart_df).mark_bar(height={"band": 0.7}).encode(
        x=alt.X("n_comments:Q", title="Comentarios en la semana de debut"),
        y=alt.Y("song:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelFontSize=12)),
        color=alt.Color("emotion:N", legend=None, scale=alt.Scale(
            domain=ui.EMOTION_ORDER, range=[ui.EMOTION_COLORS[e] for e in ui.EMOTION_ORDER])),
        tooltip=[alt.Tooltip("track_name:N", title="Canción"),
                 alt.Tooltip("main_artist:N", title="Artista"),
                 alt.Tooltip("n_comments:Q", title="Comentarios", format=","),
                 alt.Tooltip("emotion:N", title="Emoción dominante"),
                 alt.Tooltip("pct_pos:Q", title="Positivos", format=".0%"),
                 alt.Tooltip("views_gained:Q", title="Vistas ganadas", format=",")],
    )
    ui.chart(bars, height=max(160, 34 * len(chart_df)))

ui.section("Todos los debuts", "Las vistas ganadas salen de la foto diaria de estadísticas del "
           "video; las semanas anteriores al 20 de noviembre de 2026 no tienen fotos.")
ui.cards([
    ui.song_card(
        rank=int(r.rank), title=r.track_name, artist=r.artists,
        chips=[ui.emotion_chip(r.dominant_emotion_es if r.n_comments else None, "Público: "),
               ui.emotion_chip(r.lyrics_emotion_es, "Letra: ")],
        stats=[f"<b>{int(r.n_comments):,}</b> comentarios"
               + (f" · <b>{r.pct_pos:.0%}</b> positivos" if not pd.isna(r.pct_pos) else ""),
               f"Vistas ganadas en la semana: <b>{ui.fmt_views(None if pd.isna(r.views_gained) else r.views_gained)}</b>",
               f"Streams: <b>{int(r.streams):,}</b>"],
        video_id=r.youtube_video_id, delay=min(0.04 * i, 0.4))
    for i, r in enumerate(df.itertuples())
])
