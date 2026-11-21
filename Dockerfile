FROM python:3.13-slim

# cron para los trabajos batch y tzdata para trabajar en hora de México.
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

# Zona horaria del contenedor: los horarios de cron y las fechas de los logs
# quedan en hora de México.
ENV TZ=America/Mexico_City
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone

RUN pip install --no-cache-dir uv==0.8.13

ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

# Primero solo las dependencias: si no cambian, Docker reusa esta capa.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY common/ common/
COPY ingestion/ ingestion/
COPY transform/ transform/
COPY scripts/ scripts/
COPY tests/ tests/

RUN crontab ingestion/crontab
