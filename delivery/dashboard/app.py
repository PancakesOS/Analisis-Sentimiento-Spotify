"""Dashboard de Laura: «¿Qué canciones promuevo esta semana y con qué tono?»

Streamlit, en http://localhost:8501. Una página por cada pregunta de
negocio del RFC (sección 7 b), más un resumen semanal, el modelo
predictivo y la calidad de los datos. Solo lee de las vistas v_p1 a v_p7
y de las tablas del modelo.

Estructura:
  app.py        configuración, barra lateral y navegación (este archivo);
  ui.py         colores, estilos, animaciones y componentes;
  data.py       consultas a PostgreSQL con caché;
  state.py      la semana y el sello elegidos en la barra lateral;
  screens/      una página por archivo.

La semana elegida en la barra lateral aplica a todas las páginas que
muestran una sola semana; "Mi sello" resalta el sello de Laura en P7 y en
el resumen.
"""

from pathlib import Path

import streamlit as st

from delivery.dashboard import data, state, ui

ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="Pulso emocional · México", page_icon=":material/graphic_eq:",
                   layout="wide", initial_sidebar_state="expanded")
ui.inject_css()

pages = {
    "Resumen": [
        st.Page("screens/inicio.py", title="Resumen de la semana", icon=":material/space_dashboard:",
                default=True),
    ],
    "Las 7 preguntas": [
        st.Page("screens/p1_reaccion.py", title="1 · Reacción del público", icon=":material/favorite:",
                url_path="reaccion"),
        st.Page("screens/p2_anticipa.py", title="2 · ¿La reacción anticipa subidas?",
                icon=":material/trending_up:", url_path="anticipa"),
        st.Page("screens/p3_animo.py", title="3 · Ánimo del top", icon=":material/mood:",
                url_path="animo"),
        st.Page("screens/p4_artistas.py", title="4 · Letra contra público",
                icon=":material/compare_arrows:", url_path="artistas"),
        st.Page("screens/p5_permanencia.py", title="5 · Permanencia en el chart",
                icon=":material/timer:", url_path="permanencia"),
        st.Page("screens/p6_debuts.py", title="6 · Debuts de la semana", icon=":material/new_releases:",
                url_path="debuts"),
        st.Page("screens/p7_sellos.py", title="7 · Mi sello contra los demás", icon=":material/sell:",
                url_path="sellos"),
    ],
    "Análisis": [
        st.Page("screens/modelo.py", title="Modelo predictivo", icon=":material/model_training:",
                url_path="modelo"),
        st.Page("screens/calidad.py", title="Calidad de los datos", icon=":material/fact_check:",
                url_path="calidad"),
    ],
}

st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))

try:
    weeks = data.weeks()
except Exception as e:  # sin base o sin esquema: mostrar el problema en vez de una traza
    st.error("No se pudo leer la base de datos. Revisa que el servicio postgres esté encendido "
             f"(docker compose ps). Detalle: {e}")
    st.stop()

if weeks.empty:
    ui.hero("Sin datos todavía", "Falta cargar el chart",
            "Deja al menos un CSV de <b>Weekly Top Songs Mexico</b> en "
            "<code>data/landing/charts/</code>. El contenedor batch lo carga en menos de 15 minutos.")
    st.stop()

with st.sidebar:
    state.week_selector(weeks)
    state.label_selector()
    state.stale_chart_warning()
    state.freshness_note()

nav = st.navigation(pages, position="sidebar", expanded=True)
nav.run()
