"""Elegir el video oficial de una canción entre los resultados de search.list.

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
"""

import re
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
