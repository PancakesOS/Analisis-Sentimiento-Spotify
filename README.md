# Pulso emocional de la música en México

Flujo de datos de principio a fin que cruza, para cada canción del top semanal de Spotify México:

- **cuánto se escucha:** Spotify Charts, CSV semanal;
- **qué dice la letra:** LRCLIB, API de letras;
- **cómo reacciona el público:** comentarios del video oficial en YouTube.

Sobre las letras y los comentarios calcula el sentimiento y la emoción, y los entrega en un dashboard y un modelo simple para el área de marketing de un sello discográfico.

El diseño está en el RFC, [docs/rfc.md](docs/rfc.md), y el diagrama del flujo en [docs/diagrama_flujo.png](docs/diagrama_flujo.png).

## Estado

Todas las etapas del RFC funcionan de principio a fin. Desde el 21 de noviembre de 2026 el sistema junta comentarios y estadísticas; lo que falta es tiempo para acumular semanas de comentarios.

| Etapa | Qué funciona |
|---|---|
| Chart | `chart_loader` carga los CSV del chart (54 semanas, del 6 de noviembre de 2025 al 12 de noviembre de 2026) |
| Letras | `lyrics_fetcher` (LRCLIB) y `lyrics_processor` (sentimiento por estrofa): 658 de 663 canciones con letra (99 %) |
| YouTube | `youtube_client` con control de cuota, `video_finder`, `stats_snapshot`, `comments_producer` + Kafka + `consumer` |
| Comentarios | `comments_processor` (sentimiento y emoción con pysentimiento) y `aggregate_week` |
| Entrega | Vistas `v_p1` a `v_p7`, dashboard en Streamlit y modelo de regresión logística |
| Operación | `retention` (30 días), `scripts/verify.py` (11 revisiones) y pruebas |

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

    La primera construcción tarda unos minutos: la imagen trae PyTorch (solo CPU) y los dos modelos de sentimiento ya descargados (~830 MB), así que pesa unos 4 GB.

5. Abrir el dashboard en http://localhost:8501.

Al arrancar, el contenedor `batch`:

1. carga los CSV en cuanto PostgreSQL está listo;
2. en segundo plano, corre una vez cada trabajo, en orden: busca el video oficial de las canciones del top 50, toma la primera foto de estadísticas, busca las letras y calcula su sentimiento (unos 15 minutos para ~660 canciones), analiza los comentarios que ya estén en el crudo, agrega la reacción por semana y entrena el modelo si ya hay datos suficientes;
3. deja cron encendido con los horarios de la tabla.

`comments_producer` empieza a leer los comentarios de esos videos y los publica en Kafka, y `consumer` los guarda en la zona cruda. A partir de ahí, los comentarios nuevos se analizan cada hora. Las páginas de letras (P3 y P5) tienen datos desde el primer arranque; las de comentarios se llenan conforme avanza la carga histórica, que la cuota de YouTube reparte en varios días.

| Servicio | Qué hace | Cada cuánto |
|---|---|---|
| `postgres` | Base de datos | Siempre encendido |
| `kafka` | Tópico `youtube_comments` (retención de 7 días) | Siempre encendido |
| `batch` (cron) | `chart_loader` | Cada 15 minutos |
| | `video_finder` | Cada hora, a los :05; sin pendientes no gasta cuota |
| | `comments_processor` y, en cuanto termina, `aggregate_week` | Cada hora, a los :15 |
| | `lyrics_fetcher` y, en cuanto termina, `lyrics_processor` | Cada hora, a los :25; sin pendientes no hace nada |
| | `retention` | Cada hora, a los :40 |
| | `train` (modelo) | Cada hora, a los :50; solo reentrena si cambiaron los datos |
| | `stats_snapshot` | Revisa cada 10 minutos y toma la foto si la última del día tiene más de 55 minutos; sobrescribe la del día |
| `comments_producer` | Comentarios nuevos del top 50 vigente; los demás videos, una vez al día; carga histórica con tope de cuota | Ciclo cada hora |
| `consumer` | Kafka → `raw/comments/AAAA-MM-DD.log` | Continuo |
| `dashboard` | Streamlit en http://localhost:8501 (solo lectura) | Siempre encendido |

La cuota de YouTube (10,000 unidades al día, que se reinician a las 02:00 de la Ciudad de México) se reparte así:

- `youtube_client` se niega a llamar arriba de 9,000;
- las búsquedas (100 unidades cada una) y la carga histórica de comentarios paran en 7,500;
- lo que queda es para la lectura de cada hora y las estadísticas, unas 1,500 unidades al día.

## El dashboard

http://localhost:8501. Tema oscuro, con animaciones cortas (los bloques entran con un desvanecido, las cifras cuentan desde cero, las barras se llenan) que se desactivan si el sistema pide reducir el movimiento. En la barra lateral se elige la **semana del chart** (por omisión, la más reciente) y **mi sello**, que se resalta en P7 y en el resumen.

| Página | Qué responde | Vista |
|---|---|---|
| Resumen de la semana | Comentarios, reacción, índice de ánimo y las candidatas a promover, con su tono (emoción de los comentarios y de la letra) y su probabilidad de subir | `v_p1`, `v_p3`, `v_p7`, `model_prediction` |
| 1 · Reacción del público | Las canciones del top 50 con la reacción más positiva (20 comentarios o más) | `v_p1` |
| 2 · ¿La reacción anticipa subidas? | Qué porcentaje sube la semana siguiente, por quintil de reacción, y la evaluación del modelo | `v_p2`, `model_run` |
| 3 · Ánimo del top | El índice de ánimo de las letras semana a semana y su cambio contra hace 4 semanas | `v_p3` |
| 4 · Letra contra público | Artistas con letras negativas y un público feliz | `v_p4` |
| 5 · Permanencia en el chart | Semanas en el chart según el sentimiento de la letra | `v_p5`, `v_song_longevity` |
| 6 · Debuts de la semana | Las canciones nuevas que más conversación generan y con qué emoción | `v_p6` |
| 7 · Mi sello contra los demás | Peso en los streams y reacción del público por sello | `v_p7` |
| Modelo predictivo | AUC contra el baseline, qué variables pesan y la probabilidad de subir de cada canción | `model_run`, `model_prediction` |
| Calidad de los datos | Cobertura de letras y videos, frescura de cada fuente, cuota de YouTube y validación manual (sentimiento y videos) | tablas de control, `sentiment_label`, `video_check` |

El dashboard abre la sesión de PostgreSQL en modo de solo lectura y guarda cada consulta 5 minutos en caché. Las gráficas son de Altair, que viene incluido en Streamlit.

## El modelo predictivo

`delivery/model/train.py` entrena una regresión logística (scikit-learn) que estima si una canción del top 50 queda en un mejor lugar la semana siguiente.

- **Datos:** `v_model_dataset`, canciones del top 50 con 5 comentarios o más en la semana y con la semana siguiente ya cargada.
- **Variables:** reacción del público, volumen y crecimiento de comentarios, lugar, lugares que subió esta semana, semanas en el chart, si es debut o reingreso y sentimiento de la letra.
- **Evaluación:** la prueba son las 3 semanas más recientes con resultado conocido y el entrenamiento todas las anteriores (el modelo nunca ve el futuro de lo que predice). Se compara contra el baseline del RFC, «sube si subió la semana pasada»; la meta es un AUC de 0.60 o más y mejor que el baseline.
- **Salida:** métricas y coeficientes en `model_run`, el modelo en `data/state/models/` (volumen `state`) y la probabilidad de subir de cada canción de la semana más reciente en `model_prediction`.
- **Reproducible:** `random_state` fijo. Si los datos no cambiaron (md5 de los datos), no vuelve a entrenar.

## Verificación

```bash
docker compose ps                                   # los 6 servicios arriba; postgres, kafka y dashboard (healthy)
docker compose logs postgres | grep ERROR           # vacío
tail logs/*.log                                     # cada trabajo deja una línea de inicio y otra de fin
docker compose exec batch /app/.venv/bin/python -m scripts.verify          # termina con OK
docker compose exec batch /app/.venv/bin/python -m unittest discover -s tests -t .   # pruebas
```

### Validación manual

Dos criterios de éxito del RFC necesitan a una persona. Los resultados se guardan en la base (`db/05_validation.sql`) y se ven en la página «Calidad de los datos» del dashboard.

**Videos oficiales (meta: al menos 90 % correctos en 20 canciones del top 50).** `video_finder` marca como `review` los videos que no pudo confirmar con su regla (ver `transform/match.py`). Para listarlos, junto con una muestra al azar de los elegidos automáticamente:

```bash
docker compose exec batch /app/.venv/bin/python -m scripts.review_videos
```

Para medir el criterio: muestra 20 canciones al azar del top 50 vigente, pregunta una por una si el video es el oficial, guarda la respuesta en `video_check` y, si no lo es, permite pegar el enlace correcto ahí mismo:

```bash
docker compose exec batch /app/.venv/bin/python -m scripts.review_videos --revisar
docker compose exec batch /app/.venv/bin/python -m scripts.review_videos --resultado
```

Para corregir uno a mano; queda como `manual` y el poller empieza a leer el video nuevo en su siguiente ciclo:

```bash
docker compose exec batch /app/.venv/bin/python -m scripts.review_videos --set spotify:track:XXXXXXXXXXXXXXXXXXXXXX VIDEO_ID
```

**Sentimiento (meta: al menos 70 % de coincidencia en 100 comentarios).** Muestra comentarios al azar, uno por uno, y se responde 1 positivo, 2 neutro, 3 negativo o `s` para saltar uno que no se puede etiquetar (otro idioma, solo una mención). Es a ciegas: nunca enseña lo que dijo el modelo. Se puede salir con `q` y retomar después. Al final muestra la coincidencia, la kappa de Cohen, la precisión y exhaustividad por clase y la matriz de confusión:

```bash
docker compose exec batch /app/.venv/bin/python -m scripts.label_sentiment
docker compose exec batch /app/.venv/bin/python -m scripts.label_sentiment --resultado
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

**Cuidado:** esto borra también las fotos diarias de estadísticas, que no se pueden volver a pedir a YouTube (la API solo da el total de hoy), y obliga a gastar cuota otra vez en búsquedas y comentarios. Para probar desde cero sin tocar el sistema que está corriendo, usa otro nombre de proyecto: `docker compose -p pulso_prueba up -d --build`.

## Cómo funciona la carga del chart

`ingestion/chart_loader.py`, cada 15 minutos:

1. Calcula el sha256 de cada CSV de `data/landing/charts/` y omite los que ya están en `etl_loaded_file`.
2. Valida el nombre del archivo (la fecha debe ser jueves), el encabezado y las filas: tipos, rangos, URI de Spotify, y que ni el `rank` ni la `uri` se repitan. Se toleran hasta 5 lugares faltantes (ver «Decisiones que se tomaron al construir»). Un archivo inválido queda registrado como `rejected` con el motivo y no se carga nada de él.
3. Copia el archivo a la zona cruda (`raw/charts/`, volumen de Docker) con parte del sha256 en el nombre.
4. En una sola transacción, carga `dim_artist`, `dim_song`, `song_artist` y `fact_chart_week`, y registra el archivo. Si ya había otro archivo de la misma semana, la semana se reemplaza completa y el anterior queda como `replaced`.

Una canción puede llegar con otra URI: Spotify a veces se la cambia y mantiene su continuidad en el chart. Cada URI se guarda en `song_uri` apuntando a su canción (ver «Decisiones que se tomaron al construir»).

Si llegan varios archivos a la vez, se cargan de la semana más vieja a la más nueva. El título, el sello y los artistas de una canción solo se sobrescriben (SCD tipo 1) con un archivo de una semana igual o posterior a la última en que apareció.

## Estructura del repo

```text
├── docker-compose.yml       # postgres, kafka, batch (cron), comments_producer, consumer y dashboard
├── Dockerfile               # Python 3.13 con uv, cron, zona horaria de México y los modelos de sentimiento
├── pyproject.toml, uv.lock  # dependencias con versiones exactas (PyTorch solo CPU en Linux)
├── .env.example             # variables necesarias, con valores de ejemplo
├── .streamlit/config.toml   # tema del dashboard y sin estadísticas de uso
├── db/
│   ├── 01_control.sql       # etl_loaded_file, etl_quota_usage, video_watermark
│   ├── 02_star.sql          # dim_date (ya llena), dim_artist, dim_song, song_uri, song_artist y las 4 tablas de hechos
│   ├── 03_views.sql         # vistas v_p1 … v_p7 (una por pregunta) y sus vistas de apoyo
│   ├── 04_model.sql         # model_run y model_prediction
│   └── 05_validation.sql    # sentiment_label y video_check (validación manual)
├── common/jobs.py           # log con el formato del RFC, conexión a PostgreSQL y candado por trabajo
├── ingestion/
│   ├── youtube_client.py    # única puerta a la API de YouTube; lleva la cuenta de la cuota
│   ├── chart_loader.py      # carpeta de entrada → crudo → dimensiones y fact_chart_week
│   ├── lyrics_fetcher.py    # LRCLIB → raw/lyrics/<uri>.json y lyrics_status
│   ├── video_finder.py      # search.list → dim_song.youtube_video_id
│   ├── stats_snapshot.py    # videos.list → fact_video_daily
│   ├── comments_producer.py # poller de comentarios → Kafka (sin datos del autor)
│   ├── consumer.py          # Kafka → raw/comments/AAAA-MM-DD.log
│   ├── crontab              # horarios de los trabajos batch (hora de México)
│   └── entrypoint_batch.sh  # arranque del contenedor batch: variables para cron, primeras corridas y cron
├── transform/
│   ├── normalize.py         # normalización de títulos, artistas y sellos
│   ├── song_identity.py     # une las URI de Spotify que son la misma canción
│   ├── match.py             # elige el video oficial y la versión de consenso de la letra
│   ├── sentiment.py         # carga los modelos fijados de pysentimiento y analiza por lotes
│   ├── lyrics_processor.py  # letra → estrofas → sentimiento y emoción en dim_song
│   ├── comments_processor.py # crudo → fact_comment, con checkpoint por byte
│   ├── aggregate_week.py    # fact_comment → song_week_reaction
│   └── retention.py         # borra el texto de comentarios a los 29 días
├── delivery/
│   ├── dashboard/           # Streamlit: app.py, ui.py (estilo y animaciones), data.py, state.py y screens/
│   └── model/train.py       # regresión logística, evaluación contra el baseline y predicciones
├── scripts/
│   ├── verify.py            # conciliación entrada ↔ crudo ↔ base, cobertura, cuota y privacidad; termina con OK
│   ├── review_videos.py     # revisión y corrección manual de los videos
│   └── label_sentiment.py   # etiquetado a mano de 100 comentarios para validar el sentimiento
├── tests/                   # pruebas de normalización, chart, video, letras, comentarios, modelo y validación
├── data/                    # ignorada por git: landing/ (CSV descargados a mano)
├── logs/                    # ignorada por git: un .log por trabajo
└── docs/                    # RFC y diagrama
```

## Decisiones que se tomaron al construir

Estos detalles no cambian el diseño del RFC; lo completan. Los voy a pasar al RFC en la versión del 24 de noviembre.

- **Los trabajos de YouTube corren cada hora, no una vez al día.** El sistema corre en mi Mac, que se duerme. Un trabajo programado a las 23:50 perdería el día completo si a esa hora estaba dormida. En cambio, sin nada pendiente, una corrida cada hora no gasta cuota:
    - `video_finder`, a los :05;
    - `stats_snapshot` revisa cada 10 minutos y toma la foto solo si la última del día tiene más de 55 minutos: a lo más una por hora, unas 2 unidades cada una. Sobrescribe la foto del día, así que queda la última que se tomó. Si la Mac pasa dormida casi todo el día, basta con que esté despierta unos 10 minutos para que ese día tenga su foto;
    - `retention`, a los :40.
- **`video_watermark` guarda también el avance de la carga histórica** (`backfill_page_token`, `backfill_done`), la última lectura (`last_polled_at`) y si el video tiene los comentarios desactivados (`disabled_reason`). Así la carga histórica sigue al día siguiente donde se quedó, sin volver a gastar cuota.
- **Alcance de la carga histórica de comentarios:**
    - solo los videos de las canciones del top 50 de las últimas 12 semanas, como dice el RFC (unos 79 videos);
    - de cada video, solo los comentarios desde el inicio de la historia del chart (31 de octubre de 2025), porque los anteriores no caen en ninguna semana del chart;
    - la primera página de un video nuevo se lee aunque la carga histórica ya no tenga cuota ese día, así el video entra desde ya a la lectura de cada hora.
- **El mensaje de Kafka se arma con una lista blanca de 9 campos** (`comment_id`, `video_id`, `text_original`, `like_count`, `reply_count`, `published_at`, `updated_at`, `fetched_at` y `source`), en lugar de borrar los campos del autor. Así ningún dato del autor puede colarse aunque YouTube agregue campos nuevos. `verify.py` revisa que ningún mensaje del crudo traiga campos del autor.
- **El consumer no copia el texto de los mensajes a su log**, a diferencia del de la Tarea 4: el log no se borra a los 30 días.
- **La retención borra a los 29 días.** El crudo se escribe en un archivo por día de llegada; borrando el archivo cuando cumple 29 días, ningún comentario llega a los 30.
- **La regla para elegir el video oficial tiene dos casos más que el RFC.** La regla del RFC compara el canal con el artista y con el sello (`source`), y si nada coincide toma el primer resultado para revisión. Al correrla con el top 50 encontré que muchos videos oficiales los sube un sello distinto al que trae el CSV ("Rico o muerto music", "Rancho Humilde") o un canal VEVO. Por eso agregué dos casos:
    - el sello también se compara como viene en el CSV;
    - un video cuenta como oficial si el título lo dice ("Video Oficial", "Official Visualizer", "Official MV"), no es de letras y el canal no es de fans, letras ni reacciones.

    Con esto quedaron 70 videos automáticos y 3 para revisión, de 73. Las respuestas de `search.list` se guardan en `raw/search/`. Si la regla mejora, `video_finder` vuelve a elegir sobre ellas sin gastar cuota, y nunca toca las correcciones manuales.
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
- **`fact_comment.discarded`** marca los comentarios de un video que resultó no ser el oficial, como describe la sección 7 c, y también los de canciones con la letra en inglés (ver la decisión de abajo).
- **`dim_date` se llena completa** al crear la base (de 2005 a 2035), así que ningún trabajo tiene que crear fechas.
- **PostgreSQL no publica el puerto 5432:** nada fuera de Docker lo necesita, y así no choca con otro PostgreSQL en la misma computadora.
- **`common/jobs.py`** reúne lo que repetiría cada trabajo: el formato del log, la conexión y el candado.

### Letras, sentimiento, agregados, dashboard y modelo

- **El sentimiento usa los modelos de pysentimiento con inferencia directa, no `Analyzer.predict()`.** `predict()` pasa por el `Trainer` de transformers y en la Mac analizó unos 13 textos por segundo: 10,000 comentarios habrían tardado más de 25 minutos por modelo, y la meta del RFC es menos de 15 minutos en total. `transform/sentiment.py` usa el mismo preprocesamiento (`preprocess_tweet`), el mismo tokenizer, el mismo truncado a 128 tokens y los mismos modelos, en lotes ordenados por largo. Comprobé que da exactamente las mismas probabilidades. Medido en Docker, con los dos modelos (sentimiento y emoción): unos 57 comentarios por segundo, así que 10,000 comentarios tardan unos 3 minutos.
- **Los modelos se fijan por commit de Hugging Face**, además de la versión del paquete en `uv.lock`, y se descargan al construir la imagen. Los contenedores corren sin conexión a Hugging Face (`HF_HUB_OFFLINE=1`). `model_version` guarda las tres cosas: `pysentimiento-0.7.3+sent-a2cc0f6+emo-bcd6835`.
- **PyTorch se instala solo para CPU** (índice de PyTorch para Linux en `pyproject.toml`). Sin eso, en una computadora x86_64 uv bajaría también las librerías de CUDA, varios GB que aquí no se usan.
- **`lyrics_fetcher` hace una búsqueda libre si la búsqueda por título y artista no encuentra la canción** (`?q=título artista`). La necesitaron 6 de 663 canciones, en las que LRCLIB escribe distinto al artista.
- **La regla de consenso de la letra acepta a cualquier artista de la canción**, no solo al principal: LRCLIB a veces guarda una colaboración con el nombre del invitado.
- **Las letras se analizan por estrofa** (bloques separados por línea vacía; un bloque de más de 8 líneas se corta en pedazos de 4), porque el modelo trunca a 128 tokens. Las probabilidades se promedian ponderando por palabras, así que el coro que se repite pesa más, como pesa al escucharla.
- **Las letras en inglés quedan fuera de las preguntas de letras.** El modelo es para español y en inglés da resultados poco confiables. `lyrics_language` se calcula contando palabras comunes de cada idioma: 60 de 658 letras salieron en inglés (BTS, Taylor Swift, Michael Jackson, villancicos). Con las 598 en español, la cobertura sigue arriba de la meta de 85 %. La decisión está en un solo lugar, la vista `v_song`.
- **Las canciones con la letra en inglés también quedan fuera del análisis de comentarios.** El 24 de noviembre encontré que el 73 % de los comentarios (539,000 de 741,000) venían de un solo video, «SWIM» de BTS: un video global, con comentarios en inglés, coreano y muchos otros idiomas, que el modelo en español no entiende y que no representa al público de México. Su carga histórica se llevó casi toda la cuota de esos días y la mayor parte del tiempo de CPU. Desde entonces:
    - `comments_producer` ya no pide comentarios de esos videos (sí se siguen tomando sus estadísticas diarias);
    - `comments_processor` no analiza sus comentarios que ya estaban en el crudo (motivo `cancion_en_ingles` en el log);
    - `aggregate_week` marca como descartados los que ya estaban en `fact_comment`, así que salen de `song_week_reaction` y de las preguntas.

    Es la misma regla que ya usan las letras (`lyrics_language = 'en'`), así que una canción queda dentro o fuera de todo el análisis a la vez.
- **La emoción dominante no cuenta "others"**, ni en los comentarios (`dominant_emotion`) ni en las letras (`lyrics_emotion`). Casi siempre es la mayoría y no dice nada del tono; si todo es "others", queda "others" ("sin emoción clara" en el dashboard).
- **`song_week_reaction` tiene una columna más, `last_ingested_at`:** el `ingested_at` más reciente con que se calculó cada pareja canción–semana. Así `aggregate_week` sabe qué parejas recalcular sin un archivo de estado. Recalcula solo esas, no la semana completa, para no perder las parejas cuyos comentarios ya borró la retención.
- **`comments_processor` guarda un checkpoint por archivo** (el crudo tiene un archivo por día), y quita del estado los archivos que ya borró `retention`. Antes de analizar un bloque, revisa qué comentarios ya están en la base, para no gastar CPU en duplicados.
- **Los trabajos de letras, comentarios y modelo también corren cada hora**, por la misma razón que los de YouTube: sin pendientes no hacen nada y no cargan el modelo.
- **El modelo usa el crecimiento de comentarios en lugar del crecimiento de vistas.** La foto diaria de estadísticas empezó el 20 de noviembre de 2026, así que las semanas de entrenamiento no tienen ese dato. También usa los lugares que subió esta semana y si es debut o reingreso.
- **Los resultados del modelo van a la base** (`db/04_model.sql`, un archivo que el RFC no tenía): `model_run` guarda métricas y coeficientes, y `model_prediction` la probabilidad de subir de cada canción de la semana más reciente. Así el dashboard los muestra sin abrir el archivo del modelo.
- **La validación manual queda en la base** (`db/05_validation.sql`, otro archivo que el RFC no tenía). `sentiment_label` guarda solo el id del comentario, la etiqueta de la persona y la del modelo, sin el texto: así la etiqueta sobrevive cuando `retention` borra el comentario a los 30 días, y el texto nunca se copia a otro lado. `video_check` guarda qué video se revisó y si era el oficial; si se corrige, la métrica sigue midiendo lo que eligió `video_finder`, no el estado corregido.
- **Umbrales de las vistas:** P2 usa las canciones con 20 comentarios o más en la semana, igual que P1. P4 toma las últimas 12 semanas.
- **El dashboard tiene tres páginas más que el RFC:** un resumen de la semana con las canciones candidatas a promover, el modelo y la calidad de los datos.
- **El arranque del contenedor `batch` corre todos los trabajos una vez, en segundo plano**, para que un clon nuevo tenga datos en minutos y no hasta la siguiente hora.

## Datos que no están en el repo

- **Los CSV de Spotify Charts no se incluyen**, porque los términos de Spotify prohíben redistribuirlos. Cada quien los descarga con el botón oficial de *Weekly Top Songs Mexico* en charts.spotify.com y los deja en `data/landing/charts/`.
- **El archivo `.env` no se sube.** Hay que crearlo a partir de `.env.example`, con una API key propia de YouTube Data API v3.
