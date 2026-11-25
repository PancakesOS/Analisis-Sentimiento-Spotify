"""sentiment: carga los modelos de pysentimiento fijados y analiza textos por lotes.

Usa los dos modelos RoBERTuito de pysentimiento para español (Pérez et
al., 2021), entrenados con tweets, que entienden jerga y emojis:

  * sentimiento: POS, NEU o NEG, con sus tres probabilidades;
  * emoción:     joy, sadness, anger, fear, surprise, disgust u others.

Reproducibilidad (RFC, sección 9):

  * La versión de pysentimiento queda fija en uv.lock.
  * Cada modelo se fija por el commit de su repositorio en Hugging Face
    (MODELS) y se descarga al construir la imagen de Docker
    (python -m transform.sentiment --download). Al correr, los
    contenedores trabajan sin internet (HF_HUB_OFFLINE=1): el mismo texto
    da siempre el mismo resultado.
  * MODEL_VERSION junta las tres cosas y se guarda junto a cada resultado.

Velocidad: Analyzer.predict() de pysentimiento pasa por el Trainer de
transformers y en la Mac analizó unos 13 textos por segundo; con eso,
10,000 comentarios tardarían más de 25 minutos por modelo (la meta del RFC
es menos de 15 minutos en total). Aquí se usa el mismo preprocesamiento
(preprocess_tweet), el mismo tokenizer, el mismo truncado a 128 tokens y
el mismo modelo, pero con la inferencia directa: lotes ordenados por
largo, relleno solo hasta el texto más largo del lote y sin gradientes. Da
exactamente las mismas probabilidades que predict() y es unas 45 veces más
rápido (256 comentarios en 0.4 s en la Mac).
"""

import os
import sys
from dataclasses import dataclass
from importlib.metadata import version

LANG = "es"
MAX_TOKENS = 128          # el mismo límite que pone pysentimiento
MAX_CHARS = 2_000         # un comentario más largo se recorta antes de preprocesarlo
BATCH_SIZE = 64

# Repositorio, commit y archivo de pesos de cada modelo en Hugging Face. Solo
# se descarga ese archivo de pesos (los repositorios traen también copias
# para TensorFlow y en otros formatos, que inflarían la imagen).
MODELS = {
    "sentiment": ("pysentimiento/robertuito-sentiment-analysis",
                  "a2cc0f67ebd705c55191e25a05ba23d885fcc09b", "model.safetensors"),
    "emotion": ("pysentimiento/robertuito-emotion-analysis",
                "bcd6835f4d1ab1a061bd7437c9d762623c8437ad", "pytorch_model.bin"),
}

MODEL_VERSION = (f"pysentimiento-{version('pysentimiento')}"
                 f"+sent-{MODELS['sentiment'][1][:7]}+emo-{MODELS['emotion'][1][:7]}")

SENTIMENTS = ("POS", "NEU", "NEG")
EMOTIONS = ("joy", "sadness", "anger", "fear", "surprise", "disgust", "others")


@dataclass(frozen=True)
class Analysis:
    """Resultado de un texto."""
    sentiment: str                 # POS, NEU o NEG
    p_pos: float
    p_neu: float
    p_neg: float
    emotion: str                   # la emoción más probable, incluida "others"
    emotion_probas: dict[str, float]

    @property
    def score(self) -> float:
        """p_pos - p_neg, de -1 (muy negativo) a 1 (muy positivo)."""
        return self.p_pos - self.p_neg


class SentimentModel:
    """Los dos modelos cargados en memoria. Cargarlos tarda unos segundos:
    se crea una sola vez por corrida y se reusa para todos los lotes."""

    def __init__(self, threads: int | None = None):
        import torch
        from pysentimiento.preprocessing import preprocess_tweet
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        from transformers.utils import logging as hf_logging

        # Sin barras de progreso ni avisos: cron manda stderr a cron_errors.log,
        # que debe quedar vacío si todo sale bien.
        hf_logging.set_verbosity_error()
        hf_logging.disable_progress_bar()
        torch.set_num_threads(threads or int(os.environ.get("TORCH_THREADS", os.cpu_count() or 1)))
        self._torch = torch
        self._preprocess = preprocess_tweet
        self._models = {}
        for task, (repo, revision, _) in MODELS.items():
            tokenizer = AutoTokenizer.from_pretrained(repo, revision=revision)
            model = AutoModelForSequenceClassification.from_pretrained(repo, revision=revision)
            model.to("cpu").eval()
            labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
            self._models[task] = (tokenizer, model, labels)

    def _probas(self, task: str, texts: list[str]) -> list[dict[str, float]]:
        tokenizer, model, labels = self._models[task]
        # Ordenar por largo hace que cada lote rellene poco: es la mayor ganancia de velocidad.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        result: list[dict[str, float] | None] = [None] * len(texts)
        with self._torch.inference_mode():
            for start in range(0, len(order), BATCH_SIZE):
                idx = order[start:start + BATCH_SIZE]
                encoded = tokenizer([texts[i] for i in idx], padding="longest", truncation=True,
                                    max_length=MAX_TOKENS, return_tensors="pt")
                probas = self._torch.softmax(model(**encoded).logits, dim=-1).tolist()
                for i, row in zip(idx, probas):
                    result[i] = dict(zip(labels, row))
        return result

    def analyze(self, texts: list[str]) -> list[Analysis]:
        """Sentimiento y emoción de cada texto, en el mismo orden."""
        if not texts:
            return []
        prepared = [self._preprocess(t[:MAX_CHARS], lang=LANG) for t in texts]
        sentiments = self._probas("sentiment", prepared)
        emotions = self._probas("emotion", prepared)
        results = []
        for sent, emo in zip(sentiments, emotions):
            results.append(Analysis(
                sentiment=max(SENTIMENTS, key=sent.__getitem__),
                p_pos=sent["POS"], p_neu=sent["NEU"], p_neg=sent["NEG"],
                emotion=max(emo, key=emo.__getitem__),
                emotion_probas=emo,
            ))
        return results


def download() -> None:
    """Descarga los modelos fijados al caché de Hugging Face (paso del Dockerfile)."""
    from huggingface_hub import snapshot_download

    for repo, revision, weights in MODELS.values():
        path = snapshot_download(repo, revision=revision, allow_patterns=["*.json", weights])
        print(f"{repo}@{revision[:7]} -> {path}")


def check() -> None:
    """Carga los modelos sin internet y analiza dos frases de prueba (paso del Dockerfile).

    Se corre con HF_HUB_OFFLINE=1: así se comprueba que la imagen ya trae
    todo y transformers no baja nada más (con internet, para el modelo de
    emoción bajaría otra copia de los pesos en formato safetensors).
    """
    model = SentimentModel(threads=1)
    result = model.analyze(["Te amo, es la mejor canción del mundo", "Odio esta canción"])
    assert [a.sentiment for a in result] == ["POS", "NEG"], result
    print(f"modelos listos: {MODEL_VERSION}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--download"]:
        download()
    elif sys.argv[1:] == ["--check"]:
        check()
    else:
        print("Uso: python -m transform.sentiment --download | --check")
        sys.exit(2)
