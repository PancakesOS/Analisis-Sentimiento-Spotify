"""Calidad de los datos: cobertura, frescura y cuota (criterios de éxito del RFC, sección 10).

Responde "¿puedo confiar en lo que veo?": qué parte de las canciones tiene
letra y video, cuándo llegó el último dato de cada fuente y cuánta cuota
de YouTube se gasta.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from delivery.dashboard import data, ui

MEXICO = ZoneInfo("America/Mexico_City")

ui.hero("Análisis · Calidad de los datos", "¿Qué tan completos y frescos están los datos?",
        "Cobertura de letras y videos, frescura de cada fuente y uso de la cuota de YouTube. Son "
        "los criterios de éxito del RFC que se pueden medir desde la base.")

cov = data.query("""
    SELECT count(*) AS songs,
           count(*) FILTER (WHERE lyrics_status = 'found') AS found,
           count(*) FILTER (WHERE lyrics_status = 'instrumental') AS instrumental,
           count(*) FILTER (WHERE lyrics_status = 'not_found') AS not_found,
           count(*) FILTER (WHERE lyrics_status IS NULL) AS pending,
           count(*) FILTER (WHERE lyrics_language = 'es') AS spanish,
           count(*) FILTER (WHERE lyrics_language = 'en') AS english,
           count(*) FILTER (WHERE model_version IS NOT NULL) AS analyzed
    FROM dim_song
""").iloc[0]
top50 = data.query("""
    SELECT count(*) AS songs, count(s.youtube_video_id) AS with_video,
           count(*) FILTER (WHERE s.video_match = 'review') AS review,
           count(*) FILTER (WHERE s.video_match = 'manual') AS manual
    FROM fact_chart_week f JOIN dim_song s ON s.song_key = f.song_key
    WHERE f.week_key = (SELECT max(week_key) FROM fact_chart_week) AND f.rank <= 50
""").iloc[0]
comments = data.query("""
    SELECT count(*) AS total,
           count(*) FILTER (WHERE ingested_at > now() - interval '24 hours') AS last_24h,
           count(*) FILTER (WHERE discarded) AS discarded,
           count(DISTINCT video_id) AS videos
    FROM fact_comment
""").iloc[0]
quota_today = data.scalar("""
    SELECT COALESCE(sum(units), 0) FROM etl_quota_usage
    WHERE quota_date = (now() AT TIME ZONE 'America/Los_Angeles')::date
""")

songs = int(cov["songs"])
ui.kpi_row([
    ui.kpi("Canciones con letra", int(cov["found"]) / songs * 100 if songs else None, decimals=1,
           suffix=" %", note=f"{int(cov['found'])} de {songs} · meta del RFC: 85 %"),
    ui.kpi("Videos del top 50 vigente", int(top50["with_video"]), suffix=" de 50",
           note=f"{int(top50['review'])} por revisar · {int(top50['manual'])} corregidos a mano"),
    ui.kpi("Comentarios en la base", int(comments["total"]),
           note=f"{int(comments['last_24h']):,} en las últimas 24 h · "
                f"{int(comments['videos'])} videos"),
    ui.kpi("Cuota de YouTube de hoy", int(quota_today or 0), suffix=" / 10,000",
           note="tope del sistema: 9,000 (día del Pacífico)"),
])

ui.section("Frescura de cada fuente", "Cuándo llegó el último dato. Si la Mac estuvo dormida, los "
           "trabajos se ponen al día solos en la siguiente hora.")
fresh = data.query("""
    SELECT 'Chart de Spotify (última semana cargada)' AS fuente,
           (SELECT max(loaded_at) FROM etl_loaded_file WHERE status = 'loaded') AS ultimo,
           (SELECT to_char(max(week_key)::text::date, 'DD/MM/YYYY') FROM fact_chart_week) AS detalle
    UNION ALL
    SELECT 'Comentarios analizados', (SELECT max(ingested_at) FROM fact_comment),
           (SELECT count(*)::text || ' en total' FROM fact_comment)
    UNION ALL
    SELECT 'Reacción por canción y semana', (SELECT max(updated_at) FROM song_week_reaction),
           (SELECT count(*)::text || ' parejas canción–semana' FROM song_week_reaction)
    UNION ALL
    SELECT 'Foto de estadísticas de los videos', (SELECT max(captured_at) FROM fact_video_daily),
           (SELECT count(DISTINCT date_key)::text
                   || CASE WHEN count(DISTINCT date_key) = 1 THEN ' día con foto' ELSE ' días con foto' END
            FROM fact_video_daily)
    UNION ALL
    SELECT 'Letras (última búsqueda en LRCLIB)', (SELECT max(lyrics_checked_at) FROM dim_song),
           (SELECT count(*)::text || ' letras analizadas' FROM dim_song WHERE model_version IS NOT NULL)
    UNION ALL
    SELECT 'Modelo predictivo', (SELECT max(trained_at) FROM model_run),
           (SELECT count(*)::text || CASE WHEN count(*) = 1 THEN ' entrenamiento' ELSE ' entrenamientos' END
            FROM model_run)
""")
fresh["ultimo"] = pd.to_datetime(fresh["ultimo"], utc=True).dt.tz_convert(MEXICO)
now = datetime.now(MEXICO)
fresh["hace"] = fresh["ultimo"].map(
    lambda t: "—" if pd.isna(t) else
    f"hace {int((now - t).total_seconds() // 60)} min" if (now - t).total_seconds() < 3600 else
    f"hace {int((now - t).total_seconds() // 3600)} h" if (now - t).total_seconds() < 172800 else
    f"hace {int((now - t).total_seconds() // 86400)} días")
st.dataframe(fresh[["fuente", "ultimo", "hace", "detalle"]], hide_index=True, width="stretch",
             column_config={"fuente": "Fuente",
                            "ultimo": st.column_config.DatetimeColumn("Último dato",
                                                                      format="D MMM YYYY, HH:mm"),
                            "hace": "Antigüedad", "detalle": "Detalle"})

left, right = st.columns(2, gap="large")
with left:
    ui.section("Cuota de YouTube por día", "Unidades gastadas por día (del Pacífico, que es como "
               "cuenta Google). La línea punteada es el tope de 9,000 que se impone el sistema.")
    quota = data.query("""
        SELECT quota_date, method, sum(units) AS units FROM etl_quota_usage
        WHERE quota_date > (now() AT TIME ZONE 'America/Los_Angeles')::date - 14
        GROUP BY quota_date, method ORDER BY quota_date
    """)
    if quota.empty:
        ui.empty("Sin uso de cuota registrado", "")
    else:
        names = {"search.list": "Búsquedas", "commentThreads.list": "Comentarios",
                 "videos.list": "Estadísticas"}
        quota["method"] = quota["method"].map(lambda m: names.get(m, m))
        domain = ["Búsquedas", "Comentarios", "Estadísticas"]
        st.html(ui.legend(dict(zip(domain, [ui.TOKENS["pos"], "#d95926", "#199e70"]))))
        bars = alt.Chart(quota).mark_bar(size=22, cornerRadius=0).encode(
            x=alt.X("quota_date:T", title=None, axis=alt.Axis(format="%d %b", labelAngle=0)),
            y=alt.Y("sum(units):Q", title="Unidades", stack="zero"),
            color=alt.Color("method:N", legend=None, scale=alt.Scale(
                domain=domain, range=[ui.TOKENS["pos"], "#d95926", "#199e70"])),
            order=alt.Order("method:N"),
            stroke=alt.value(ui.TOKENS["surface"]), strokeWidth=alt.value(1),
            tooltip=[alt.Tooltip("quota_date:T", title="Día", format="%d %b %Y"),
                     alt.Tooltip("method:N", title="Uso"),
                     alt.Tooltip("units:Q", title="Unidades", format=",")],
        )
        cap = alt.Chart().mark_rule(strokeDash=[5, 4], color=ui.TOKENS["neg"]).encode(
            y=alt.datum(9000))
        ui.chart(bars + cap, height=280)

with right:
    ui.section("Comentarios por día de publicación", "Últimos 30 días. Incluye los que llegaron "
               "en la carga histórica.")
    per_day = data.query("""
        SELECT d.date AS day, count(*) AS n FROM fact_comment f
        JOIN dim_date d ON d.date_key = f.date_key
        WHERE d.date > current_date - 30 AND NOT f.discarded
        GROUP BY d.date ORDER BY d.date
    """)
    if per_day.empty:
        ui.empty("Sin comentarios analizados todavía", "comments_processor corre cada hora.")
    else:
        hover = alt.selection_point(fields=["day"], nearest=True, on="pointerover", empty=False)
        base = alt.Chart(per_day).encode(x=alt.X("day:T", title=None,
                                                 axis=alt.Axis(format="%d %b", labelAngle=0)))
        line = base.mark_area(line={"color": "#d95926", "strokeWidth": 2.5},
                              color="rgba(217,89,38,0.18)", interpolate="monotone").encode(
            y=alt.Y("n:Q", title="Comentarios"))
        pts = base.mark_point(size=70, color="#d95926").encode(
            y="n:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("day:T", title="Día", format="%d %b %Y"),
                     alt.Tooltip("n:Q", title="Comentarios", format=",")]).add_params(hover)
        ui.chart(line + pts, height=280)

ui.section("Validación manual", "Dos criterios de éxito del RFC que revisa una persona: si el "
           "modelo de sentimiento coincide con alguien que lee el comentario, y si el video elegido "
           "es el oficial.")
labels = data.query("""
    SELECT human_sentiment, model_sentiment, count(*) AS n FROM sentiment_label
    WHERE human_sentiment <> 'SKIP' GROUP BY 1, 2
""")
videos = data.query("""
    SELECT count(*) AS total, count(*) FILTER (WHERE is_correct) AS correct FROM video_check
""").iloc[0]
n_labels = int(labels["n"].sum()) if not labels.empty else 0
hits = int(labels.loc[labels["human_sentiment"] == labels["model_sentiment"], "n"].sum()) \
    if n_labels else 0
n_videos, ok_videos = int(videos["total"]), int(videos["correct"])
ui.kpi_row([
    ui.kpi("Coincidencia del sentimiento", hits / n_labels * 100 if n_labels else None,
           decimals=1, suffix=" %",
           note=f"{hits} de {n_labels} comentarios etiquetados a mano · meta: 70 % con 100"),
    ui.kpi("Videos oficiales correctos", ok_videos / n_videos * 100 if n_videos else None,
           suffix=" %", note=f"{ok_videos} de {n_videos} revisados · meta: 90 % con 20"),
])
if n_labels:
    names = {"POS": "Positivo", "NEU": "Neutro", "NEG": "Negativo"}
    order = list(names.values())
    grid = pd.DataFrame([(h, m) for h in names for m in names], columns=["human_sentiment",
                                                                         "model_sentiment"])
    grid = grid.merge(labels, how="left").fillna({"n": 0})
    grid["persona"] = grid["human_sentiment"].map(names)
    grid["modelo"] = grid["model_sentiment"].map(names)
    grid["share"] = grid["n"] / grid.groupby("persona")["n"].transform("sum").replace(0, 1)
    base = alt.Chart(grid).encode(
        x=alt.X("modelo:N", sort=order, title="Lo que dijo el modelo",
                axis=alt.Axis(orient="top", labelAngle=0)),
        y=alt.Y("persona:N", sort=order, title="Lo que dijo la persona"))
    cells = base.mark_rect(cornerRadius=4, stroke=ui.TOKENS["bg"], strokeWidth=2).encode(
        color=alt.Color("share:Q", legend=None,
                        scale=alt.Scale(domain=[0, 1], range=[ui.TOKENS["surface_2"],
                                                               ui.TOKENS["accent"]])),
        tooltip=[alt.Tooltip("persona:N", title="Persona"), alt.Tooltip("modelo:N", title="Modelo"),
                 alt.Tooltip("n:Q", title="Comentarios"),
                 alt.Tooltip("share:Q", title="De la fila", format=".0%")])
    text = base.mark_text(fontSize=15, fontWeight=600, color=ui.TOKENS["text"]).encode(
        text=alt.Text("n:Q", format="d"))
    st.caption("Matriz de confusión: cada fila suma los comentarios que la persona etiquetó con "
               "esa clase; la diagonal son las coincidencias.")
    ui.chart(cells + text, height=240)
if n_labels < 100 or n_videos < 20:
    ui.callout("<b>Cómo completar la validación.</b> Etiquetar comentarios (a ciegas: no se "
               "muestra lo que dijo el modelo, y el texto no se guarda): "
               "<code>docker compose exec batch /app/.venv/bin/python -m scripts.label_sentiment"
               "</code>. Revisar videos: <code>docker compose exec batch /app/.venv/bin/python -m "
               "scripts.review_videos --revisar</code>.", warn=True)

ui.section("Letras", "Estado de la búsqueda en LRCLIB y del análisis de sentimiento.")
lyr = pd.DataFrame([
    {"estado": "Con letra, en español", "canciones": int(cov["spanish"])},
    {"estado": "Con letra, en inglés (fuera del análisis)", "canciones": int(cov["english"])},
    {"estado": "Con letra, sin analizar todavía",
     "canciones": int(cov["found"]) - int(cov["spanish"]) - int(cov["english"])},
    {"estado": "Instrumental", "canciones": int(cov["instrumental"])},
    {"estado": "Sin letra en LRCLIB (se reintenta cada 7 días)", "canciones": int(cov["not_found"])},
    {"estado": "Sin buscar todavía", "canciones": int(cov["pending"])},
])
st.dataframe(lyr[lyr["canciones"] > 0], hide_index=True, width="stretch",
             column_config={"estado": "Estado", "canciones": st.column_config.ProgressColumn(
                 "Canciones", format="%d", min_value=0, max_value=max(songs, 1))})

ui.section("Archivos del chart cargados", "Cada CSV se registra con su sha256: cargar dos veces el "
           "mismo archivo no duplica nada.")
files = data.query("""
    SELECT file_name, chart_week_end, row_count, status, loaded_at FROM etl_loaded_file
    ORDER BY loaded_at DESC LIMIT 12
""")
st.dataframe(files, hide_index=True, width="stretch", column_config={
    "file_name": "Archivo", "chart_week_end": st.column_config.DateColumn("Semana que termina",
                                                                         format="D MMM YYYY"),
    "row_count": "Filas", "status": "Estado",
    "loaded_at": st.column_config.DatetimeColumn("Cargado", format="D MMM YYYY, HH:mm")})
