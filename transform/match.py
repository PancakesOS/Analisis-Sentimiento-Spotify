"""Emparejar una canción del chart con su video oficial y con su letra.

Dos reglas deterministas: choose_video (YouTube) y choose_lyrics (LRCLIB).

--- choose_video: el video oficial entre los resultados de search.list ---

El primer resultado de YouTube muchas veces no es el oficial: para
"Fuerza Regida Suzuki" es el de un canal de letras ("Amazing Lyrics"). El
oficial está en el canal del sello que trae la columna source del chart
(Atlantic Records). Regla, en este orden de preferencia y siempre entre
resultados cuyo título contiene el título de la canción:

  1. el canal coincide con un artista de la canción ("KarolGVEVO",
     "Fuerza Regida") y no es un canal automático "- Topic";
  2. el canal coincide con el sello, normalizado o como viene en el CSV
     ("Atlantic Records", "DEL Records");
  3. el título dice que es oficial ("Video Oficial", "Official
     Visualizer", "Official MV"), no es de letras y el canal no es de
     fans, letras, karaoke o reacciones. Cubre los videos que sube otro
     sello o el sello del artista ("Rico o muerto music", "Rancho
     Humilde"), que no siempre es el que trae el CSV;
  4. el canal automático "Artista - Topic" (audio oficial, sin video);
  5. si nada coincide: el primer resultado, marcado como "review" para
     revisarlo a mano.

Las reglas 1 a 4 se marcan como "auto". Dentro de cada regla se respeta el
orden de YouTube, así que el resultado es determinista para la misma
respuesta.

--- choose_lyrics: la versión de la letra entre los resultados de LRCLIB ---

LRCLIB regresa todas las versiones que ha subido la gente: "Suzuki" tiene
16, con títulos y duraciones distintas, y "BbY WOW" tiene dos letras que
empiezan diferente ("Oh, my baby" y "¿Y mi baby?"). Regla del RFC
(sección 7 d):

  1. Se quedan los resultados cuyo título normalizado es igual al de la
     canción y cuyo artista contiene a alguno de sus artistas.
  2. Se agrupan por sus primeras 3 líneas normalizadas y se elige el grupo
     más grande: la versión de consenso.
  3. Dentro del grupo, la duración más común; si hay empate, el id más
     bajo. Los empates entre grupos también se resuelven por el id más
     bajo, así que el resultado no depende del orden de la respuesta.

Si todos los resultados que pasan el filtro son instrumentales, la
canción queda como instrumental.
"""

import re
from collections import Counter
from dataclasses import dataclass

from transform.normalize import compact, normalize_title

MIN_LABEL_LEN = 4  # un sello de 3 letras ("rca") coincidiría con demasiados canales
OFFICIAL_RE = re.compile(r"\b(?:official|oficial)\b|\bMV\b|\bvisuali[sz]er\b", re.IGNORECASE)
LYRICS_RE = re.compile(r"\b(?:letras?|lyrics?)\b", re.IGNORECASE)
# Canales que suben copias aunque el título diga "oficial" ("Victor Mendivil fans").
UNOFFICIAL_CHANNEL_RE = re.compile(r"\b(?:fans?|lyrics?|letras?|karaoke|reacci\w*|reaction\w*)\b",
                                   re.IGNORECASE)


def label_keys(label_norm: str | None, label_raw: str | None) -> list[str]:
    """Formas del sello para buscarlo en el nombre del canal."""
    keys = []
    if label_norm:
        keys.append(compact(label_norm))
    if label_raw:
        first = re.sub(r"\([^)]*\)", " ", label_raw).split("/")[0]
        keys.append(compact(first))
    return [k for k in dict.fromkeys(keys) if len(k) >= MIN_LABEL_LEN]


@dataclass(frozen=True)
class VideoChoice:
    video_id: str
    channel_title: str
    video_title: str
    match: str          # auto o review
    rule: str           # artista, sello, oficial, topic o primero


def choose_video(items: list[dict], track_name: str, artists: list[str],
                 label_norm: str | None, label_raw: str | None = None) -> VideoChoice | None:
    """Elige el video de una respuesta de search.list. None si no hay resultados."""
    videos = [it for it in items if it.get("id", {}).get("videoId")]
    if not videos:
        return None

    title_key = compact(normalize_title(track_name))
    artist_keys = [k for k in (compact(a) for a in artists) if len(k) >= 3]
    labels = label_keys(label_norm, label_raw)

    def describe(item: dict) -> tuple[str, str, str]:
        snippet = item.get("snippet", {})
        return item["id"]["videoId"], snippet.get("channelTitle", ""), snippet.get("title", "")

    def classify(item: dict) -> str | None:
        _, channel, title = describe(item)
        if title_key and title_key not in compact(title):
            return None
        channel_key = compact(channel)
        is_topic = channel.strip().lower().endswith("- topic")
        if any(k in channel_key for k in artist_keys):
            return "topic" if is_topic else "artista"
        if any(k in channel_key for k in labels):
            return "sello"
        if (OFFICIAL_RE.search(title) and not LYRICS_RE.search(title)
                and not UNOFFICIAL_CHANNEL_RE.search(channel)):
            return "oficial"
        return None

    rules = [classify(it) for it in videos]
    for rule in ("artista", "sello", "oficial", "topic"):
        for item, item_rule in zip(videos, rules):
            if item_rule == rule:
                return VideoChoice(*describe(item), match="auto", rule=rule)
    return VideoChoice(*describe(videos[0]), match="review", rule="primero")


# --- Letras -----------------------------------------------------------------

CONSENSUS_LINES = 3
_LRC_TIMESTAMP = re.compile(r"^\s*(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+")


@dataclass(frozen=True)
class LyricsChoice:
    status: str                 # found o instrumental
    lrclib_id: int | None       # None si es instrumental
    candidates: int             # resultados que pasaron el filtro de título y artista
    group_size: int             # tamaño del grupo de consenso


def lyrics_text(record: dict) -> str:
    """Texto plano de un resultado de LRCLIB.

    Usa plainLyrics; si viene vacío pero hay syncedLyrics (formato LRC,
    "[00:12.34] línea"), les quita las marcas de tiempo.
    """
    plain = (record.get("plainLyrics") or "").strip()
    if plain:
        return plain
    synced = record.get("syncedLyrics") or ""
    return "\n".join(_LRC_TIMESTAMP.sub("", line) for line in synced.splitlines()).strip()


def consensus_key(text: str) -> str:
    """Las primeras 3 líneas con texto, normalizadas: identifican una versión de la letra."""
    lines = [compact(line) for line in text.splitlines()]
    return "|".join([line for line in lines if line][:CONSENSUS_LINES])


def lyrics_candidates(results: list[dict], track_name: str, artists: list[str]) -> list[dict]:
    """Resultados cuyo título coincide con la canción y cuyo artista incluye a uno de sus artistas."""
    title_key = compact(normalize_title(track_name))
    artist_keys = [k for k in (compact(a) for a in artists) if len(k) >= 2]
    keep = []
    for record in results:
        if not isinstance(record, dict) or not isinstance(record.get("id"), int):
            continue
        if compact(normalize_title(record.get("trackName") or "")) != title_key:
            continue
        artist_field = compact(record.get("artistName") or "")
        if not any(k in artist_field for k in artist_keys):
            continue
        keep.append(record)
    return keep


def choose_lyrics(results: list[dict], track_name: str, artists: list[str]) -> LyricsChoice | None:
    """Elige la versión de consenso. None si ningún resultado corresponde a la canción."""
    candidates = lyrics_candidates(results, track_name, artists)
    if not candidates:
        return None
    with_text = [(r, lyrics_text(r)) for r in candidates if not r.get("instrumental")]
    with_text = [(r, text) for r, text in with_text if consensus_key(text)]
    if not with_text:
        if any(r.get("instrumental") for r in candidates):
            return LyricsChoice("instrumental", None, len(candidates), 0)
        return None

    groups: dict[str, list[dict]] = {}
    for record, text in with_text:
        groups.setdefault(consensus_key(text), []).append(record)
    # El grupo más grande; si empatan, el que tiene el id más bajo.
    group = min(groups.values(), key=lambda g: (-len(g), min(r["id"] for r in g)))
    durations = Counter(round(r.get("duration") or 0) for r in group)
    best = max(durations.values())
    common = {d for d, n in durations.items() if n == best}
    chosen = min((r for r in group if round(r.get("duration") or 0) in common),
                 key=lambda r: r["id"])
    return LyricsChoice("found", chosen["id"], len(candidates), len(group))
