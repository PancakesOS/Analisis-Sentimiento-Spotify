"""La semana y el sello que Laura elige en la barra lateral, compartidos por todas las páginas."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from delivery.dashboard import data, ui

MEXICO = ZoneInfo("America/Mexico_City")


def week_selector(weeks: pd.DataFrame) -> None:
    options = weeks["week_key"].tolist()
    ends = dict(zip(weeks["week_key"], weeks["week_end"]))
    st.selectbox("Semana del chart", options, index=0, key="week_key",
                 format_func=lambda k: ui.week_label(ends[k]) + (" · última" if k == options[0] else ""),
                 help="Semana de viernes a jueves, como la publica Spotify. Aplica a las páginas "
                      "que muestran una sola semana.")
    st.session_state["week_end"] = ends[st.session_state["week_key"]]
    st.session_state["latest_week_key"] = options[0]
    st.session_state["latest_week_end"] = ends[options[0]]


def missing_charts(latest_end: date, today: date) -> int:
    """Cuántos charts semanales ya publicó Spotify y no se han cargado.

    La semana termina en jueves y Spotify publica su chart al día
    siguiente: el de la semana que termina en latest_end + 7 sale el
    día latest_end + 8.
    """
    days = (today - latest_end).days
    return max(0, (days - 1) // 7)


def stale_chart_warning() -> None:
    """Avisa en la barra lateral si falta descargar el chart de alguna semana.

    La descarga es manual (los términos de Spotify prohíben automatizarla),
    así que el dashboard es donde Laura se entera de que falta.
    """
    latest_end = st.session_state.get("latest_week_end")
    if latest_end is None:
        return
    missing = missing_charts(latest_end, datetime.now(MEXICO).date())
    if missing == 0:
        return
    newest = latest_end + timedelta(days=7 * missing)
    what = ("el chart de la semana que terminó el " + ui.fmt_date(newest) if missing == 1 else
            f"{missing} charts semanales (el más reciente terminó el {ui.fmt_date(newest)})")
    st.warning(f"Falta cargar {what}. Descárgalo de charts.spotify.com y déjalo en "
               "data/landing/charts/; se carga solo en menos de 15 minutos.",
               icon=":material/event_busy:")


def label_selector() -> None:
    labels = data.query("""
        SELECT label_norm, max(label_name) AS label_name, sum(streams) AS streams
        FROM v_p7 WHERE recency <= 12 AND label_norm <> '(sin sello)'
        GROUP BY label_norm ORDER BY streams DESC
    """)
    if labels.empty:
        return
    names = dict(zip(labels["label_norm"], labels["label_name"]))
    st.selectbox("Mi sello", labels["label_norm"].tolist(), index=0, key="my_label",
                 format_func=names.get,
                 help="Se resalta en la página 7 y en el resumen. Ordenados por streams de las "
                      "últimas 12 semanas.")
    st.session_state["my_label_name"] = names[st.session_state["my_label"]]


def freshness_note() -> None:
    last_comment = data.scalar("SELECT max(ingested_at) FROM fact_comment")
    last_reaction = data.scalar("SELECT max(updated_at) FROM song_week_reaction")
    parts = []
    if last_comment is not None:
        parts.append(f"Último comentario analizado: {_ago(last_comment)}")
    if last_reaction is not None:
        parts.append(f"Reacción agregada: {_ago(last_reaction)}")
    parts.append("Los datos se actualizan cada hora; esta página guarda 5 minutos lo que consulta.")
    st.caption("  \n".join(parts))


def _ago(ts: datetime) -> str:
    minutes = int((datetime.now(MEXICO) - ts).total_seconds() // 60)
    if minutes < 1:
        return "hace un momento"
    if minutes < 60:
        return f"hace {minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"hace {hours} h"
    return f"hace {hours // 24} días"


def week() -> tuple[int, date]:
    """(week_key, week_end) de la semana elegida."""
    return st.session_state["week_key"], st.session_state["week_end"]


def is_latest() -> bool:
    return st.session_state["week_key"] == st.session_state["latest_week_key"]


def my_label() -> tuple[str | None, str | None]:
    return st.session_state.get("my_label"), st.session_state.get("my_label_name")
