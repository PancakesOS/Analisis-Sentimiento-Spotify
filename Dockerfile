FROM python:3.13-slim

# cron para los trabajos batch y tzdata para trabajar en hora de México.
# Los paquetes se bajan por HTTPS: por HTTP simple, una caché intermedia de
# la red llegó a entregar archivos alterados ("Hash Sum mismatch") y la
# construcción fallaba. Los reintentos cubren las fallas pasajeras.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

# Zona horaria del contenedor: los horarios de cron y las fechas de los logs
# quedan en hora de México.
ENV TZ=America/Mexico_City
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone

RUN pip install --no-cache-dir uv==0.8.13

ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    HF_HOME=/opt/huggingface

WORKDIR /app

# Primero solo las dependencias: si no cambian, Docker reusa esta capa.
# PyTorch se instala en su versión solo para CPU (ver pyproject.toml). El
# caché de descargas de uv se monta aparte (no queda dentro de la imagen,
# que si no pesaría el doble) y se reusa entre construcciones.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# Los modelos de sentimiento y emoción, fijados por commit, quedan dentro de
# la imagen (~830 MB). Después se comprueba que cargan sin internet, y desde
# ahí los contenedores trabajan sin conexión a Hugging Face: el mismo texto
# da siempre el mismo resultado.
COPY transform/__init__.py transform/sentiment.py transform/
RUN /app/.venv/bin/python -m transform.sentiment --download \
    && HF_HUB_OFFLINE=1 /app/.venv/bin/python -m transform.sentiment --check
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY common/ common/
COPY ingestion/ ingestion/
COPY transform/ transform/
COPY delivery/ delivery/
COPY scripts/ scripts/
COPY tests/ tests/
COPY .streamlit/ .streamlit/

RUN crontab ingestion/crontab
