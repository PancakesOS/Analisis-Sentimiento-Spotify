"""Sistema visual del dashboard: colores, estilos, animaciones, tema de las gráficas y componentes.

Todas las páginas usan estas piezas para verse igual:

  * Colores (TOKENS): un tema oscuro. Los colores de las gráficas se
    eligieron para distinguirse también con daltonismo (se validaron por
    pares contra el fondo oscuro):
      - polaridad: positivo azul, neutro gris, negativo rojo (dos polos
        opuestos con un gris en medio, nunca verde contra rojo);
      - emociones: un color fijo por emoción en todas las páginas.
  * Animaciones: los bloques entran con un desvanecido corto hacia arriba,
    las cifras grandes cuentan desde cero, las barras de las listas se
    llenan y las tarjetas se elevan al pasar el cursor. Todo dura menos de
    un segundo y se desactiva si el sistema pide reducir el movimiento
    (prefers-reduced-motion).
  * Tema de Altair: fondo transparente, ejes y cuadrícula discretos, texto
    del mismo color que el resto de la página.
"""

import html
from datetime import date

import altair as alt
import streamlit as st

TOKENS = {
    "bg": "#0e0f13",
    "surface": "#16181e",
    "surface_2": "#1d2028",
    "border": "rgba(255,255,255,0.08)",
    "text": "#eceae6",
    "text_2": "#b9b7ae",
    "muted": "#8a8880",
    "grid": "#262830",
    "axis": "#3a3c44",
    "accent": "#3987e5",
    "accent_2": "#9085e9",
    "pos": "#3987e5",
    "neu": "#6b6a66",
    "neg": "#e66767",
    "good": "#0ca30c",
}

EMOTION_COLORS = {
    "alegría": "#c98500",
    "tristeza": "#3987e5",
    "enojo": "#e66767",
    "miedo": "#9085e9",
    "sorpresa": "#d55181",
    "asco": "#0ca30c",
    "sin emoción clara": "#6b6a66",
}
EMOTION_ORDER = list(EMOTION_COLORS)
SENTIMENT_COLORS = {"Positiva": TOKENS["pos"], "Neutra": TOKENS["neu"], "Negativa": TOKENS["neg"]}
SENTIMENT_ES = {"POS": "Positiva", "NEU": "Neutra", "NEG": "Negativa"}

MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

CSS = """
<style>
:root {
  --bg: %(bg)s; --surface: %(surface)s; --surface-2: %(surface_2)s; --border: %(border)s;
  --text: %(text)s; --text-2: %(text_2)s; --muted: %(muted)s;
  --accent: %(accent)s; --accent-2: %(accent_2)s; --pos: %(pos)s; --neu: %(neu)s; --neg: %(neg)s;
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
}

/* Solo el texto: los iconos de Streamlit usan su propia fuente (Material Symbols). */
html, body, .stApp, .stMarkdown, [data-testid="stSidebarNav"] a span:not([data-testid="stIconMaterial"]) {
  font-family: %(font)s;
}
.stApp {
  background:
    radial-gradient(1200px 600px at 85%% -10%%, rgba(144,133,233,0.10), transparent 60%%),
    radial-gradient(900px 500px at -10%% 10%%, rgba(57,135,229,0.10), transparent 60%%),
    var(--bg);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 2.2rem; max-width: 1280px; }
[data-testid="stSidebar"] { background: rgba(22,24,30,0.85); border-right: 1px solid var(--border); }
h1, h2, h3 { letter-spacing: -0.02em; }

/* Entrada de cada bloque: desvanecido corto hacia arriba, escalonado. */
@keyframes fade-up { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"],
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {
  animation: fade-up 0.55s var(--ease-out) both;
}
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > *:nth-child(2) { animation-delay: 0.04s; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > *:nth-child(3) { animation-delay: 0.08s; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > *:nth-child(4) { animation-delay: 0.12s; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > *:nth-child(5) { animation-delay: 0.16s; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > *:nth-child(n+6) { animation-delay: 0.2s; }

/* Encabezado de cada página. */
.hero { position: relative; padding: 0.2rem 0 1.2rem; }
.hero .eyebrow {
  display: inline-flex; align-items: center; gap: 0.6rem;
  font-size: 0.78rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--text-2);
}
.hero h1 {
  font-size: clamp(1.8rem, 3.2vw, 2.6rem); font-weight: 800; line-height: 1.1;
  margin: 0.5rem 0 0.5rem; padding: 0;
  background: linear-gradient(90deg, #eceae6 0%%, #b9c8ff 45%%, #c9b8ff 70%%, #eceae6 100%%);
  background-size: 220%% 100%%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 9s ease-in-out infinite alternate;
}
@keyframes shimmer { from { background-position: 0%% 50%%; } to { background-position: 100%% 50%%; } }
.hero p { color: var(--text-2); font-size: 1.02rem; max-width: 760px; margin: 0; line-height: 1.55; }
.hero .question {
  margin-top: 0.9rem; display: inline-block; padding: 0.45rem 0.9rem; border-radius: 999px;
  background: rgba(57,135,229,0.12); border: 1px solid rgba(57,135,229,0.30);
  color: #cfe0f8; font-size: 0.9rem;
}

/* Ecualizador: el pulso de la marca. */
.eq { display: inline-flex; align-items: flex-end; gap: 2px; height: 14px; }
.eq span { width: 3px; border-radius: 2px; background: var(--accent); animation: eq 1.1s ease-in-out infinite; }
.eq span:nth-child(2) { animation-delay: -0.3s; background: var(--accent-2); }
.eq span:nth-child(3) { animation-delay: -0.6s; }
.eq span:nth-child(4) { animation-delay: -0.15s; background: var(--accent-2); }
@keyframes eq { 0%%, 100%% { height: 4px; } 50%% { height: 14px; } }

/* Cifras grandes. */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin: 0.3rem 0 0.6rem; }
.kpi {
  background: linear-gradient(180deg, var(--surface-2), var(--surface));
  border: 1px solid var(--border); border-radius: 16px; padding: 16px 18px;
  transition: transform 0.25s var(--ease-out), border-color 0.25s;
  animation: fade-up 0.6s var(--ease-out) both;
}
.kpi:nth-child(2) { animation-delay: 0.06s; } .kpi:nth-child(3) { animation-delay: 0.12s; }
.kpi:nth-child(4) { animation-delay: 0.18s; }
.kpi:hover { transform: translateY(-2px); border-color: rgba(255,255,255,0.16); }
.kpi .label { font-size: 0.8rem; color: var(--text-2); font-weight: 500; }
.kpi .value { font-size: 1.9rem; font-weight: 700; letter-spacing: -0.02em; margin-top: 4px;
  font-variant-numeric: tabular-nums; color: var(--text); }
.kpi .unit { font-size: 0.55em; font-weight: 600; color: var(--text-2); margin-left: 0.3em;
  letter-spacing: 0; }
.kpi .unit.pre { margin-left: 0; margin-right: 0.1em; font-size: 0.8em; }
.kpi .note { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
.kpi .delta { font-size: 0.8rem; font-weight: 600; margin-top: 4px; }
.delta.up { color: #6fb6ff; } .delta.down { color: #ff9a9a; } .delta.flat { color: var(--muted); }

/* Tarjetas de canción. */
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.card {
  position: relative; overflow: hidden;
  background: linear-gradient(180deg, var(--surface-2), var(--surface));
  border: 1px solid var(--border); border-radius: 16px; padding: 16px 18px 14px;
  transition: transform 0.25s var(--ease-out), border-color 0.25s, box-shadow 0.25s;
  animation: fade-up 0.6s var(--ease-out) both;
}
.card:hover { transform: translateY(-3px); border-color: rgba(255,255,255,0.18);
  box-shadow: 0 12px 30px rgba(0,0,0,0.35); }
.card .rank { font-size: 0.75rem; font-weight: 700; color: var(--muted); letter-spacing: 0.06em; }
.card .title { font-size: 1.05rem; font-weight: 700; margin-top: 6px; line-height: 1.25; color: var(--text); }
.card .artist { font-size: 0.86rem; color: var(--text-2); margin-top: 2px; }
.card .meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.card .stat { font-size: 0.8rem; color: var(--text-2); margin-top: 10px; }
.card .stat b { color: var(--text); font-variant-numeric: tabular-nums; }
.card a { color: #8cb8f0; text-decoration: none; font-size: 0.8rem; }
.card a:hover { text-decoration: underline; }

.chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
  font-size: 0.75rem; font-weight: 600; color: var(--text);
  background: rgba(255,255,255,0.06); border: 1px solid var(--border);
}
.chip i { width: 8px; height: 8px; border-radius: 50%%; display: inline-block; }

/* Barra de polaridad: se llena al aparecer. */
.polarity { display: flex; height: 8px; border-radius: 999px; overflow: hidden; margin-top: 12px;
  background: rgba(255,255,255,0.06); gap: 2px; }
.polarity span { display: block; height: 100%%; transform-origin: left; animation: grow 0.9s var(--ease-out) both; }
.polarity .p { background: var(--pos); } .polarity .n { background: var(--neu); } .polarity .g { background: var(--neg); }
@keyframes grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }

/* Barras de probabilidad. */
.meter { height: 6px; border-radius: 999px; background: rgba(255,255,255,0.07); margin-top: 6px; overflow: hidden; }
.meter span { display: block; height: 100%%; border-radius: 999px; transform-origin: left;
  background: linear-gradient(90deg, var(--accent), var(--accent-2)); animation: grow 1s var(--ease-out) both; }

/* Notas y secciones. */
.section { margin: 1.6rem 0 0.4rem; }
.section h3 { font-size: 1.15rem; font-weight: 700; margin: 0; color: var(--text); }
.section p { color: var(--text-2); font-size: 0.9rem; margin: 0.25rem 0 0; }
.callout {
  border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 12px;
  background: rgba(57,135,229,0.07); padding: 12px 16px; color: var(--text-2); font-size: 0.9rem;
  line-height: 1.5;
}
.callout b { color: var(--text); }
.callout.warn { border-left-color: #c98500; background: rgba(201,133,0,0.08); }
.legend { display: flex; flex-wrap: wrap; gap: 14px; color: var(--text-2); font-size: 0.8rem; margin: 2px 0 6px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.empty {
  border: 1px dashed rgba(255,255,255,0.14); border-radius: 16px; padding: 28px; text-align: center;
  color: var(--text-2); background: rgba(255,255,255,0.02);
}
.empty b { color: var(--text); display: block; font-size: 1.02rem; margin-bottom: 4px; }
.footer { color: var(--muted); font-size: 0.78rem; margin-top: 2.4rem; padding-top: 1rem;
  border-top: 1px solid var(--border); }

/* Gráficas y tablas: contenedor con borde suave. */
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] {
  background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 12px 8px 4px;
}
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
""" % {**TOKENS, "font": FONT}

# Las cifras con data-count cuentan desde cero al aparecer (formato es-MX).
COUNT_UP_JS = """
<script>
(function () {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.querySelectorAll('[data-count]:not([data-done])').forEach(function (el) {
    el.setAttribute('data-done', '1');
    const target = parseFloat(el.dataset.count);
    const decimals = parseInt(el.dataset.decimals || '0', 10);
    const prefix = el.dataset.prefix || '', suffix = el.dataset.suffix || '';
    const fmt = new Intl.NumberFormat('es-MX', {minimumFractionDigits: decimals,
                                                maximumFractionDigits: decimals});
    const sign = el.dataset.signed === '1';
    const show = v => (sign && v > 0 ? '+' : '') + prefix + fmt.format(v) + suffix;
    if (reduce || !isFinite(target)) { el.textContent = show(target); return; }
    const start = performance.now(), duration = 900;
    function step(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = show(target * eased);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });
})();
</script>
"""


def inject_css() -> None:
    st.html(CSS)


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_date(d: date) -> str:
    """"19 nov 2026"."""
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def week_label(week_end: date) -> str:
    """"6 al 12 nov 2026" para la semana que termina el 12 de noviembre."""
    start = date.fromordinal(week_end.toordinal() - 6)
    if start.month == week_end.month:
        return f"{start.day} al {week_end.day} {MONTHS[week_end.month - 1]} {week_end.year}"
    return (f"{start.day} {MONTHS[start.month - 1]} al {week_end.day} "
            f"{MONTHS[week_end.month - 1]} {week_end.year}")


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def hero(eyebrow: str, title: str, text: str, question: str | None = None) -> None:
    q = f'<div class="question">{esc(question)}</div>' if question else ""
    st.html(f"""
    <div class="hero">
      <div class="eyebrow"><span class="eq"><span></span><span></span><span></span><span></span></span>
        {esc(eyebrow)}</div>
      <h1>{esc(title)}</h1>
      <p>{text}</p>
      {q}
    </div>""")


def section(title: str, text: str | None = None) -> None:
    p = f"<p>{text}</p>" if text else ""
    st.html(f'<div class="section"><h3>{esc(title)}</h3>{p}</div>')


def callout(text: str, warn: bool = False) -> None:
    st.html(f'<div class="callout{" warn" if warn else ""}">{text}</div>')


def empty(title: str, text: str) -> None:
    st.html(f'<div class="empty"><b>{esc(title)}</b>{text}</div>')


def kpi(label: str, value: float | int | None, *, decimals: int = 0, suffix: str = "",
        prefix: str = "", note: str = "", delta: float | None = None, delta_suffix: str = "",
        delta_decimals: int = 2, signed: bool = False) -> str:
    """HTML de una cifra grande. value None se muestra como un guion."""
    if value is None:
        shown = '<div class="value">—</div>'
    else:
        # La unidad va en su propio elemento (más pequeña y gris): st.html recorta los espacios
        # de las orillas de un texto, así que "18 de 50" quedaría "18de 50".
        pre = f'<span class="unit pre">{esc(prefix.strip())}</span>' if prefix.strip() else ""
        unit = f'<span class="unit">{esc(suffix.strip())}</span>' if suffix.strip() else ""
        shown = (f'<div class="value">{pre}<span data-count="{float(value)}" '
                 f'data-decimals="{decimals}" data-signed="{1 if signed else 0}">0</span>'
                 f'{unit}</div>')
    delta_html = ""
    if delta is not None:
        cls = "up" if delta > 0 else "down" if delta < 0 else "flat"
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "●"
        delta_html = (f'<div class="delta {cls}">{arrow} {delta:+.{delta_decimals}f}{esc(delta_suffix)}'
                      f'</div>')
    note_html = f'<div class="note">{note}</div>' if note else ""
    return f'<div class="kpi"><div class="label">{esc(label)}</div>{shown}{delta_html}{note_html}</div>'


def kpi_row(items: list[str]) -> None:
    st.html(f'<div class="kpis">{"".join(items)}</div>{COUNT_UP_JS}', unsafe_allow_javascript=True)


def chip(text: str, color: str | None = None) -> str:
    dot = f'<i style="background:{color}"></i>' if color else ""
    return f'<span class="chip">{dot}{esc(text)}</span>'


def emotion_chip(emotion_es: str | None, prefix: str = "") -> str:
    if not emotion_es:
        return chip(f"{prefix}sin datos")
    return chip(f"{prefix}{emotion_es}", EMOTION_COLORS.get(emotion_es))


def polarity_bar(pos: float | None, neu: float | None, neg: float | None) -> str:
    if pos is None:
        return ""
    return (f'<div class="polarity" title="Positivos {pos:.0%}, neutros {neu:.0%}, negativos {neg:.0%}">'
            f'<span class="p" style="width:{pos * 100:.1f}%"></span>'
            f'<span class="n" style="width:{neu * 100:.1f}%"></span>'
            f'<span class="g" style="width:{neg * 100:.1f}%"></span></div>')


def meter(fraction: float) -> str:
    return f'<div class="meter"><span style="width:{max(0.0, min(1.0, fraction)) * 100:.1f}%"></span></div>'


def legend(items: dict[str, str]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{c}"></i>{esc(k)}</span>' for k, c in items.items()) + "</div>"


def youtube_link(video_id: str | None) -> str:
    if not video_id:
        return ""
    return f'<a href="https://www.youtube.com/watch?v={esc(video_id)}" target="_blank">Ver video oficial</a>'


def footer(text: str) -> None:
    st.html(f'<div class="footer">{text}</div>')


def fmt_int(n) -> str:
    return "—" if n is None else f"{int(n):,}"


def fmt_pct(x, decimals: int = 0) -> str:
    return "—" if x is None else f"{float(x) * 100:.{decimals}f} %"


def fmt_views(n) -> str:
    if n is None:
        return "sin datos"
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.0f} mil"
    return f"{n:.0f}"


# --- Tema de Altair -----------------------------------------------------------

@alt.theme.register("pulso", enable=True)
def pulso_theme() -> alt.theme.ThemeConfig:
    t = TOKENS
    return {
        "config": {
            "background": "transparent",
            "font": FONT,
            "padding": {"left": 6, "right": 14, "top": 8, "bottom": 4},
            "autosize": {"type": "fit", "contains": "padding"},
            "view": {"stroke": None},
            "axis": {
                "domainColor": t["axis"], "gridColor": t["grid"], "gridWidth": 1,
                "tickColor": t["axis"], "labelColor": t["muted"], "titleColor": t["text_2"],
                "labelFont": FONT, "titleFont": FONT, "labelFontSize": 11, "titleFontSize": 12,
                "titleFontWeight": 500, "labelPadding": 6, "titlePadding": 10,
            },
            "legend": {"labelColor": t["text_2"], "titleColor": t["text_2"], "labelFontSize": 12,
                       "titleFontSize": 12, "titleFontWeight": 500, "symbolType": "circle",
                       "orient": "top", "direction": "horizontal"},
            "title": {"color": t["text"], "font": FONT, "fontSize": 14, "fontWeight": 600,
                      "anchor": "start", "offset": 12},
            "line": {"strokeWidth": 2.5},
            "point": {"filled": True, "size": 70},
            "bar": {"cornerRadiusEnd": 4},
            "rule": {"color": t["axis"]},
            "text": {"color": t["text_2"], "font": FONT},
            "range": {"category": [t["pos"], "#d95926", "#199e70", "#c98500", "#d55181",
                                   "#0ca30c", t["accent_2"], t["neg"]]},
        }
    }


def chart(spec, height: int | None = None) -> None:
    """Muestra una gráfica de Altair con el tema del dashboard, a todo lo ancho."""
    if height:
        spec = spec.properties(height=height)
    st.altair_chart(spec, theme=None, width="stretch")


def song_card(*, rank, title, artist, chips: list[str] | None = None, stats: list[str] | None = None,
              polarity: tuple | None = None, prob_up: float | None = None,
              video_id: str | None = None, delay: float = 0.0) -> str:
    """Tarjeta de una canción: lugar, título, artista, etiquetas, cifras y barra de polaridad."""
    chips_html = f'<div class="meta">{"".join(chips)}</div>' if chips else ""
    stats_html = "".join(f'<div class="stat">{s}</div>' for s in stats or [])
    polarity_html = polarity_bar(*polarity) if polarity else ""
    prob_html = ""
    if prob_up is not None:
        prob_html = (f'<div class="stat">Probabilidad de subir la próxima semana: '
                     f'<b>{prob_up:.0%}</b></div>{meter(prob_up)}')
    link = f'<div style="margin-top:10px">{youtube_link(video_id)}</div>' if video_id else ""
    return (f'<div class="card" style="animation-delay:{delay:.2f}s">'
            f'<div class="rank">#{esc(rank)} EN EL CHART</div>'
            f'<div class="title">{esc(title)}</div><div class="artist">{esc(artist)}</div>'
            f'{chips_html}{polarity_html}{stats_html}{prob_html}{link}</div>')


def cards(items: list[str]) -> None:
    st.html(f'<div class="cards">{"".join(items)}</div>')
