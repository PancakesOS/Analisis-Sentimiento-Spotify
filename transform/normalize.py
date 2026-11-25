"""Normalización de títulos, artistas y sellos.

Las tres fuentes nombran distinto a la misma canción. El #1 de la semana
del 12 de noviembre de 2026 es "Suzuki (from GTAVI: The Album)" en el
chart, "Suzuki" en varias versiones de LRCLIB y otra cosa en cada video
de YouTube. Estas funciones dejan los nombres en una forma comparable:

  * normalize_text:   minúsculas, sin acentos y con un solo espacio.
  * normalize_artist: lo mismo; es la llave de dim_artist.
  * normalize_title:  además quita "(from …)", "(feat. …)", "(with …)" y
                      sufijos como " - Remix" o " - En Vivo".
  * normalize_label:  además se queda con el primer sello y quita lo que
                      cambia entre filas del mismo sello: paréntesis,
                      "by …", "LLC", "Records", "Music"…
  * split_artists:    separa artist_names del CSV ("A, B, C"), sin partir
                      los nombres que llevan coma ("Tyler, The Creator").

Son reglas deterministas: el mismo texto da siempre el mismo resultado.
"""

import re
import unicodedata

_SPACES = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})

# "(from GTAVI: The Album)", "(feat. T3r Elemento)", "[with benny blanco]"…
_TITLE_EXTRA_PAREN = re.compile(
    r"\s*[(\[]\s*(?:from|feat|ft|featuring|with|con)\b\.?[^)\]]*[)\]]", re.IGNORECASE)
# "Canción feat. Alguien" sin paréntesis.
_TITLE_FEAT_TAIL = re.compile(r"\s+(?:feat|ft|featuring)\b\.?\s.*$", re.IGNORECASE)
# " - Remix", " - En Vivo", " - 2019 Remasterizado"…
_TITLE_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")
# Puntuación al inicio o al final ("/Perro Fiel", "¿…?"); no quita un ")" final.
_EDGE_PUNCT = re.compile(r"^[\W_]+|[^\w)]+$")

# Artistas cuyo nombre lleva coma. El CSV separa a los artistas con ", ",
# así que sin esta lista "Tyler, The Creator" quedaría como dos artistas.
# Ninguno ha aparecido en el chart de México; si aparece otro, se agrega aquí.
COMMA_ARTISTS = (
    "Tyler, The Creator",
    "Earth, Wind & Fire",
    "Crosby, Stills, Nash & Young",
    "Crosby, Stills & Nash",
    "Emerson, Lake & Palmer",
    "Peter, Paul and Mary",
)
_COMMA_ARTISTS_RE = re.compile(
    r"(?<![^,\s])(" + "|".join(re.escape(n) for n in COMMA_ARTISTS) + r")(?![^,\s])",
    re.IGNORECASE)

_LABEL_PAREN = re.compile(r"\([^)]*\)")
_LABEL_BY = re.compile(r"\s+(?:distributed\s+by|by|presenta)\s+.*$", re.IGNORECASE)
_LABEL_LEADING_YEAR = re.compile(r"^\d{4}\s+")
_LABEL_CORP = re.compile(
    r"[\s,]+(?:llc|inc|corp|corporation|ltd|srl|s\.?\s?l|s\.?\s?a(?:\.?\s+de\s+c\.?\s?v)?)\.?$")
_LABEL_GENERIC = re.compile(r"\s+(?:records|music|entertainment|label)$")
_MIN_LABEL_LEN = 3  # no dejar "k" a partir de "K Music"


def normalize_text(text: str) -> str:
    """Minúsculas, sin acentos (también ñ → n) y con espacios simples."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_QUOTES))
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SPACES.sub(" ", without_marks.casefold()).strip()


def normalize_artist(name: str) -> str:
    """Llave de un artista: "Carín León" y "CARIN LEON" son el mismo."""
    return normalize_text(name)


def normalize_title(title: str) -> str:
    """Título comparable entre el chart, LRCLIB y YouTube.

    "Suzuki (from GTAVI: The Album)" → "suzuki"
    "Tiroteo - Remix"                → "tiroteo"
    "/Perro Fiel (with benny blanco)" → "perro fiel"
    """
    text = _TITLE_EXTRA_PAREN.sub("", title)
    text = _TITLE_FEAT_TAIL.sub("", text)
    text = _TITLE_DASH_SUFFIX.sub("", text)
    return _EDGE_PUNCT.sub("", normalize_text(text))


def search_title(title: str) -> str:
    """Título para buscar en YouTube: sin "(from …)" ni invitados, con acentos.

    A diferencia de normalize_title, conserva " - Remix" o " - En Vivo",
    porque el video de un remix es otro video.
    """
    text = _TITLE_EXTRA_PAREN.sub("", title)
    text = _TITLE_FEAT_TAIL.sub("", text)
    return _EDGE_PUNCT.sub("", _SPACES.sub(" ", text).strip())


def compact(text: str) -> str:
    """Solo letras y números, normalizados: "Karol G" → "karolg", "KarolGVEVO" → "karolgvevo"."""
    return re.sub(r"[\W_]+", "", normalize_text(text))


def normalize_label(source: str | None) -> str | None:
    """Sello comparable, para agrupar las variantes que trae la columna source.

    "Josa Records (Neton Vega)"       → "josa"
    "Rico o Muerto by Baja Sound LLC" → "rico o muerto"
    "Bichota/Interscope Records"      → "bichota"
    Regresa None si la columna viene vacía.
    """
    if source is None or not source.strip():
        return None
    text = _LABEL_PAREN.sub(" ", source)
    text = text.split("/")[0]
    text = _LABEL_BY.sub("", text)
    text = _LABEL_LEADING_YEAR.sub("", normalize_text(text))
    previous = None
    while previous != text:
        previous = text
        text = _LABEL_CORP.sub("", text).strip(" ,.-")
        without_generic = _LABEL_GENERIC.sub("", text)
        if len(without_generic) >= _MIN_LABEL_LEN:
            text = without_generic
    return text or None


def split_artists(artist_names: str) -> list[str]:
    """Separa artist_names ("Fuerza Regida, Grand Theft Auto VI") en una lista.

    Conserva el orden (el primero es el artista principal) y quita
    vacíos y repetidos según su forma normalizada. Los nombres de
    COMMA_ARTISTS no se parten.
    """
    protected: list[str] = []

    def protect(match: re.Match) -> str:
        protected.append(match.group(1))
        return f"\x00{len(protected) - 1}\x00"

    artists, seen = [], set()
    for name in _COMMA_ARTISTS_RE.sub(protect, artist_names).split(","):
        name = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], name)
        name = _SPACES.sub(" ", name).strip()
        norm = normalize_artist(name)
        if norm and norm not in seen:
            seen.add(norm)
            artists.append(name)
    return artists
