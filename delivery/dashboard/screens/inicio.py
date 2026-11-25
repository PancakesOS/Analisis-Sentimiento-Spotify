"""Resumen de la semana: lo que Laura necesita para decidir en un vistazo.

Junta las respuestas principales: cuánta conversación hubo, qué tan
positiva fue, el ánimo de las letras del top y las canciones candidatas a
promover (las de reacción más positiva, con su tono y, si hay modelo, su
probabilidad de subir).
"""

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, state, ui

week_key, week_end = state.week()
label_norm, label_name = state.my_label()

ui.hero(f"Semana del {ui.week_label(week_end)}", "¿Qué canciones promuevo y con qué tono?",
        "Cada semana se cruza el top 50 de Spotify México con el sentimiento de sus letras "
        "(LRCLIB) y la reacción del público en los comentarios del video oficial de YouTube.")

reaction = data.query("""
    SELECT sum(r.n_comments) AS n_comments,
           sum(r.pct_pos * r.n_comments) / NULLIF(sum(r.n_comments), 0) AS pct_pos,
           sum(r.avg_score * r.n_comments) / NULLIF(sum(r.n_comments), 0) AS avg_score,
           count(*) FILTER (WHERE r.n_comments >= 20) AS songs_with_reaction
    FROM fact_chart_week f
    JOIN song_week_reaction r ON r.week_key = f.week_key AND r.song_key = f.song_key
    WHERE f.week_key = %s AND f.rank <= 50
""", (week_key,))
mood = data.query("SELECT mood_index, change_4w, streams_coverage FROM v_p3 WHERE week_key = %s",
                  (week_key,))
r0 = reaction.iloc[0] if not reaction.empty else None
m0 = mood.iloc[0] if not mood.empty else None
n_comments = None if r0 is None or r0["n_comments"] is None else int(r0["n_comments"])

ui.kpi_row([
    ui.kpi("Comentarios del top 50", n_comments, note="publicados en la semana"),
    ui.kpi("Comentarios positivos", None if r0 is None or r0["pct_pos"] is None
           else float(r0["pct_pos"]) * 100, decimals=0, suffix=" %",
           note="ponderado por número de comentarios"),
    ui.kpi("Índice de ánimo de las letras", None if m0 is None or m0["mood_index"] is None
           else float(m0["mood_index"]), decimals=2, signed=True,
           delta=None if m0 is None or m0["change_4w"] is None else float(m0["change_4w"]),
           delta_suffix=" contra hace 4 semanas", note="de −1 (triste) a 1 (alegre)"),
    ui.kpi("Canciones con reacción medible", None if r0 is None else int(r0["songs_with_reaction"]),
           suffix=" de 50", note="20 comentarios o más en la semana"),
])

ui.section("Candidatas para promover",
           "Las canciones del top 50 con la reacción más positiva del público (20 comentarios o "
           "más). El tono sugerido es la emoción que más aparece en sus comentarios; también se "
           "muestra la de la letra.")
candidates = data.query("""
    SELECT p.rank, p.track_name, p.artists, p.n_comments, p.pct_pos, p.pct_neu, p.pct_neg,
           p.avg_score, p.dominant_emotion_es, emotion_es(s.lyrics_emotion) AS lyrics_emotion_es,
           p.youtube_video_id, mp.prob_up
    FROM v_p1 p
    JOIN v_song s ON s.song_key = p.song_key
    LEFT JOIN model_prediction mp ON mp.week_key = p.week_key AND mp.song_key = p.song_key
    WHERE p.week_key = %s
    ORDER BY p.pct_pos DESC, p.avg_score DESC
    LIMIT 6
""", (week_key,))
if candidates.empty:
    ui.empty("Todavía no hay reacción medible para esta semana",
             "Los comentarios llegan cada hora y se analizan a los 15 minutos de cada hora. "
             "La carga histórica de comentarios se reparte en varios días por la cuota de YouTube.")
else:
    ui.cards([
        ui.song_card(
            rank=int(c.rank), title=c.track_name, artist=c.artists,
            chips=[ui.emotion_chip(c.dominant_emotion_es, "Público: "),
                   ui.emotion_chip(c.lyrics_emotion_es, "Letra: ")],
            polarity=(float(c.pct_pos), float(c.pct_neu), float(c.pct_neg)),
            stats=[f"<b>{c.pct_pos:.0%}</b> positivos de <b>{int(c.n_comments):,}</b> comentarios"],
            prob_up=None if pd.isna(c.prob_up) else float(c.prob_up),
            video_id=c.youtube_video_id, delay=0.05 * i)
        for i, c in enumerate(candidates.itertuples())
    ])
    st.html(ui.legend({"Comentarios positivos": ui.TOKENS["pos"], "Neutros": ui.TOKENS["neu"],
                       "Negativos": ui.TOKENS["neg"]}))

left, right = st.columns([3, 2], gap="large")
with left:
    ui.section("Ánimo de las letras del top 50", "Índice de ánimo de las últimas 26 semanas "
               "(promedio del sentimiento de las letras ponderado por streams).")
    trend = data.query("SELECT week_end, mood_index FROM v_p3 WHERE recency <= 26 "
                       "AND mood_index IS NOT NULL ORDER BY week_end")
    if trend.empty:
        ui.empty("Sin letras analizadas todavía", "lyrics_processor corre cada hora.")
    else:
        base = alt.Chart(trend).encode(x=alt.X("week_end:T", title=None,
                                               axis=alt.Axis(format="%d %b", labelAngle=0)))
        area = base.mark_area(line={"color": ui.TOKENS["accent"], "strokeWidth": 2.5},
                              color=alt.Gradient(gradient="linear", stops=[
                                  alt.GradientStop(color="rgba(57,135,229,0.0)", offset=0),
                                  alt.GradientStop(color="rgba(57,135,229,0.35)", offset=1)],
                                  x1=1, x2=1, y1=1, y2=0)).encode(
            y=alt.Y("mood_index:Q", title="Índice de ánimo"))
        hover = alt.selection_point(fields=["week_end"], nearest=True, on="pointerover",
                                    empty=False)
        points = base.mark_point(size=80, color=ui.TOKENS["accent"]).encode(
            y="mood_index:Q",
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("week_end:T", title="Semana que termina", format="%d %b %Y"),
                     alt.Tooltip("mood_index:Q", title="Índice", format="+.3f")],
        ).add_params(hover)
        zero = alt.Chart().mark_rule(strokeDash=[4, 4], color=ui.TOKENS["axis"]).encode(
            y=alt.datum(0))
        ui.chart(area + points + zero, height=260)

with right:
    ui.section(f"Mi sello: {label_name or 'sin elegir'}",
               "Su peso en el chart y la reacción del público esta semana.")
    if label_norm:
        mine = data.query("""
            SELECT songs_top200, songs_top50, best_rank, pct_streams, n_comments, avg_reaction
            FROM v_p7 WHERE week_key = %s AND label_norm = %s
        """, (week_key, label_norm))
        overall = data.scalar("""
            SELECT sum(r.avg_score * r.n_comments) / NULLIF(sum(r.n_comments), 0)
            FROM song_week_reaction r JOIN fact_chart_week f
              ON f.week_key = r.week_key AND f.song_key = r.song_key
            WHERE r.week_key = %s
        """, (week_key,))
        if mine.empty:
            ui.empty("Sin canciones esta semana", "Tu sello no aparece en el top 200 de esta semana.")
        else:
            m = mine.iloc[0]
            if pd.isna(m["avg_reaction"]):
                reaction_note = "sin comentarios suficientes"
            elif overall is not None:
                reaction_note = (f"reacción {float(m['avg_reaction']):+.2f} contra "
                                 f"{float(overall):+.2f} del chart")
            else:
                reaction_note = f"reacción {float(m['avg_reaction']):+.2f}"
            ui.kpi_row([
                ui.kpi("Canciones en el top 200", int(m["songs_top200"]),
                       note=f"{int(m['songs_top50'])} en el top 50 · mejor lugar #{int(m['best_rank'])}"),
                ui.kpi("Streams de la semana", float(m["pct_streams"]) * 100, decimals=1,
                       suffix=" %", note=reaction_note),
            ])
    st.page_link("screens/p7_sellos.py", label="Comparar con los demás sellos",
                 icon=":material/arrow_forward:")

ui.footer("Fuentes: Spotify Charts (descarga manual semanal), LRCLIB y YouTube Data API v3. "
          "Sentimiento y emoción: modelos RoBERTuito de pysentimiento para español. "
          "El texto de los comentarios se borra a los 30 días; solo se conservan los agregados.")
