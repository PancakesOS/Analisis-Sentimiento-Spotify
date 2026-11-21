# Pulso emocional de la música en México

Flujo de datos de principio a fin que cruza, para cada canción del top semanal de Spotify México:

- **cuánto se escucha:** Spotify Charts, CSV semanal;
- **qué dice la letra:** LRCLIB, API de letras;
- **cómo reacciona el público:** comentarios del video oficial en YouTube.

Sobre las letras y los comentarios calcula el sentimiento y la emoción, y los entrega en un dashboard y un modelo simple para el área de marketing de un sello discográfico.

El diseño está en el RFC, [docs/rfc.md](docs/rfc.md), y el diagrama del flujo en [docs/diagrama_flujo.png](docs/diagrama_flujo.png).

## Estado

| Semana | Qué funciona |
|---|---|
| 1 | PostgreSQL con las tablas de control y el modelo estrella completo; contenedor batch con cron; `chart_loader` carga los CSV del chart; `scripts/verify.py` concilia la carga |
| 2 | *Pendiente:* `youtube_client`, `video_finder`, `stats_snapshot`, `comments_producer`, Kafka y `consumer` |
| 3 | *Pendiente:* letras, sentimiento, agregados y vistas `v_p1` a `v_p7` |
| 4 | *Pendiente:* dashboard y modelo |

## Cómo correrlo desde cero

Requisitos: Docker Desktop (o Docker Engine con Compose v2) y git.

1. Clonar el repo:

    ```bash
    git clone https://github.com/PancakesOS/Analisis-Sentimiento-Spotify.git
    cd Analisis-Sentimiento-Spotify
    ```

2. Crear `.env` a partir del ejemplo y poner una contraseña de PostgreSQL (solo letras y números) y una API key de YouTube Data API v3 propia:

    ```bash
    cp .env.example .env
    ```

3. Descargar al menos un CSV del chart y dejarlo en `data/landing/charts/`:
    1. Entrar a https://charts.spotify.com/charts/view/regional-mx-weekly/latest con una cuenta gratuita de Spotify.
    2. Presionar el ícono de descarga (arriba a la derecha). Baja un archivo `regional-mx-weekly-AAAA-MM-DD.csv`, donde la fecha es el jueves en que termina la semana.
    3. Con el selector *Week of …* se descargan semanas anteriores.

    ```bash
    mkdir -p data/landing/charts
    mv ~/Downloads/regional-mx-weekly-*.csv data/landing/charts/
    ```

4. Levantar todo:

    ```bash
    docker compose up -d --build
    ```

El contenedor `batch` carga los CSV en cuanto PostgreSQL está listo, y después revisa la carpeta cada 15 minutos. Un CSV que se agregue después se carga solo en la siguiente revisión.

## Verificación

```bash
docker compose ps                                   # postgres (healthy) y batch, arriba
docker compose logs postgres | grep ERROR           # vacío
cat logs/chart_loader.log                           # una línea de inicio, una por archivo cargado y una de fin
docker compose exec batch /app/.venv/bin/python -m scripts.verify          # termina con OK
docker compose exec batch /app/.venv/bin/python -m unittest discover -s tests -t .   # pruebas
```

Para consultar la base:

```bash
docker compose exec postgres psql -U admin -d pulso
```

```sql
SELECT f.rank, s.track_name, a.artist_name, f.streams
FROM fact_chart_week f
JOIN dim_song s USING (song_key)
JOIN dim_artist a ON a.artist_key = s.main_artist_key
WHERE f.week_key = (SELECT max(week_key) FROM fact_chart_week)
ORDER BY f.rank
LIMIT 10;
```

`chart_loader` se puede correr a mano las veces que sea; un archivo que ya se cargó se omite:

```bash
docker compose exec batch sh -c 'cd /app && /app/.venv/bin/python -m ingestion.chart_loader'
```

Para empezar de cero, borrando la base y la zona cruda (los CSV de `data/landing/` no se tocan):

```bash
docker compose down -v && docker compose up -d --build
```

## Cómo funciona la carga del chart

`ingestion/chart_loader.py`, cada 15 minutos:

1. Calcula el sha256 de cada CSV de `data/landing/charts/` y omite los que ya están en `etl_loaded_file`.
2. Valida el nombre del archivo (la fecha debe ser jueves), el encabezado y las filas: tipos, rangos, URI de Spotify, y que ni el `rank` ni la `uri` se repitan. Se toleran hasta 5 lugares faltantes (ver «Decisiones que se tomaron al construir»). Un archivo inválido queda registrado como `rejected` con el motivo y no se carga nada de él.
3. Copia el archivo a la zona cruda (`raw/charts/`, volumen de Docker) con parte del sha256 en el nombre.
4. En una sola transacción, carga `dim_artist`, `dim_song`, `song_artist` y `fact_chart_week`, y registra el archivo. Si ya había otro archivo de la misma semana, la semana se reemplaza completa y el anterior queda como `replaced`.

Una canción puede llegar con otra URI: Spotify a veces se la cambia y mantiene su continuidad en el chart. Cada URI se guarda en `song_uri` apuntando a su canción (ver «Decisiones que se tomaron al construir»).

Si llegan varios archivos a la vez, se cargan de la semana más vieja a la más nueva. El título, el sello y los artistas de una canción solo se sobrescriben (SCD tipo 1) con un archivo de una semana igual o posterior a la última en que apareció.

## Estructura del repo (hasta ahora)

```text
├── docker-compose.yml       # postgres y batch (cron); Kafka, producer y consumer llegan en la semana 2
├── Dockerfile               # Python 3.13 con uv, cron y zona horaria de México
├── pyproject.toml, uv.lock  # dependencias con versiones exactas
├── .env.example             # variables necesarias, con valores de ejemplo
├── db/
│   ├── 01_control.sql       # etl_loaded_file, etl_quota_usage, video_watermark
│   └── 02_star.sql          # dim_date (ya llena), dim_artist, dim_song, song_uri, song_artist y las 4 tablas de hechos
├── common/jobs.py           # log con el formato del RFC, conexión a PostgreSQL y candado por trabajo
├── ingestion/
│   ├── chart_loader.py      # carpeta de entrada → crudo → dimensiones y fact_chart_week
│   ├── crontab              # horarios de los trabajos batch (hora de México)
│   └── entrypoint_batch.sh  # arranque del contenedor batch: variables para cron, primera carga y cron
├── transform/
│   ├── normalize.py         # normalización de títulos, artistas y sellos
│   └── song_identity.py     # une las URI de Spotify que son la misma canción
├── scripts/verify.py        # conciliación entrada ↔ crudo ↔ base; termina con OK
├── tests/                   # pruebas de normalize y de la validación del chart
├── data/                    # ignorada por git: landing/ (CSV descargados a mano)
├── logs/                    # ignorada por git: un .log por trabajo
└── docs/                    # RFC y diagrama
```

## Decisiones que se tomaron al construir

Estos detalles no cambian el diseño del RFC; lo completan. Los voy a pasar al RFC en la versión del 24 de noviembre.

- **Una canción puede tener varias URI de Spotify (tabla `song_uri`).** El RFC usaba la URI como llave única, pero al cargar las 54 semanas del 2025-11-06 al 2026-11-12 encontré que Spotify cambia la URI de una canción que ya está en el chart, y a veces regresa a la anterior. Aun así, el chart conserva su continuidad. Sin esta tabla, 53 cambios de URI partían en varias canciones lo que es una sola: se cortaba su historia para las preguntas P2 y P5 y para el modelo, y se gastaría cuota buscando otra vez el mismo video. Una URI nueva se une a una canción existente si las dos tienen el mismo título normalizado y el mismo artista principal, y además se cumple una de estas reglas:
    - su `previous_rank` lleva a esa canción en la semana anterior (39 casos);
    - reingresa al chart con `weeks_on_chart` una semana mayor que la última aparición de esa canción (14 casos). "El Azul", de Junior H, llega a 146 semanas, sale y reingresa con otra URI en la 147.

    Si las semanas llegan en desorden, un paso de conciliación une las canciones que quedaron partidas. Lo probé cargando las 54 semanas de la más nueva a la más vieja, y el resultado es idéntico fila por fila al de la carga en orden. Quedan 663 canciones con 716 URI. `verify.py` revisa que no quede ninguna canción partida. Las reglas están en `transform/song_identity.py`.
- **Un chart puede traer hasta 5 lugares faltantes.** Spotify borra de los charts históricos las canciones que retira de la plataforma: el CSV del 2026-02-05 no trae el lugar 34, y en la semana siguiente nadie tiene `previous_rank = 34`. Rechazar esa semana la perdería para las preguntas que comparan una semana con la siguiente. Se carga con un WARNING en el log, y los lugares faltantes quedan en `etl_loaded_file.detail`.
- **`etl_loaded_file` registra también los archivos rechazados y reemplazados** (columna `status`). Un archivo inválido no se vuelve a intentar cada 15 minutos, y queda la historia de qué archivo dio cada semana. Solo puede haber un archivo vigente por semana.
- **`dim_song` tiene tres columnas más:**
    - `label_norm`, el sello normalizado para la pregunta P7. Por ejemplo, "Josa Records (Neton Vega)" queda como "josa";
    - `lyrics_checked_at`, para la regla de reintentar las letras `not_found` a los 7 días;
    - `video_checked_at`, para no repetir una búsqueda de 100 unidades de cuota.
- **`video_match` acepta `review`, además de `auto` y `manual`:** es el caso en que ningún canal coincide y se toma el primer resultado para revisarlo a mano.
- **`fact_comment.discarded`** marca los comentarios de un video que resultó no ser el oficial, como describe la sección 7 c.
- **`dim_date` se llena completa** al crear la base (de 2005 a 2035), así que ningún trabajo tiene que crear fechas.
- **PostgreSQL no publica el puerto 5432:** nada fuera de Docker lo necesita, y así no choca con otro PostgreSQL en la misma computadora.
- **`common/jobs.py`** reúne lo que repetiría cada trabajo: el formato del log, la conexión y el candado.

## Datos que no están en el repo

- **Los CSV de Spotify Charts no se incluyen**, porque los términos de Spotify prohíben redistribuirlos. Cada quien los descarga con el botón oficial de *Weekly Top Songs Mexico* en charts.spotify.com y los deja en `data/landing/charts/`.
- **El archivo `.env` no se sube.** Hay que crearlo a partir de `.env.example`, con una API key propia de YouTube Data API v3.
