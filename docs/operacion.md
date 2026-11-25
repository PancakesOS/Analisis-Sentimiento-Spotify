# Operación: logs, pruebas y qué hacer cuando algo falla

Este documento es el manual para operar el sistema: dónde ver qué pasó, cómo se prueba y qué hacer con cada falla. Todos los comandos se corren desde la carpeta del repo.

## 1. Revisión rápida (5 comandos)

Cuando algo parece estar mal, en este orden:

```bash
docker compose ps                                                        # 1. ¿los 6 servicios están arriba?
docker compose exec batch /app/.venv/bin/python -m scripts.status        # 2. ¿cada trabajo corrió, cuándo y con errores?
docker compose exec batch /app/.venv/bin/python -m scripts.verify        # 3. ¿la base cuadra con el crudo? (12 revisiones)
tail -n 20 logs/<trabajo>.log                                            # 4. el detalle del trabajo que falló
cat logs/cron_errors.log                                                 # 5. lo que un trabajo escribió a la salida de errores
```

En el dashboard, la página «Calidad de los datos» muestra la antigüedad del último dato de cada fuente, y la barra lateral avisa si falta descargar un chart.

## 2. Logs

Cada trabajo escribe en `logs/<trabajo>.log` (carpeta montada desde la Mac, así que se lee sin entrar al contenedor), con el formato de la sección 7 f del RFC:

```text
2026-11-24T19:46:01-0600 INFO comments_processor: fin; duracion=5s leidos=164419 insertados=20 descartados=164399 (vacio=54, cancion_en_ingles=164345) lotes=1 comentarios_por_segundo=19 errores=0
```

- **Cada corrida deja una línea de `inicio` y una de `fin`.** La de fin trae la duración, lo leído, cargado y descartado (con el motivo), la cuota y `errores=`. Una corrida sin línea de fin se interrumpió (el contenedor se detuvo o la Mac se apagó a la mitad).
- **Niveles:** `INFO` para lo normal; `WARNING` para algo que se manejó solo (un archivo con lugares faltantes, una corrida omitida porque otra seguía en curso); `ERROR` para algo que no se pudo hacer y se reintenta después.
- **Nunca se escribe el texto de un comentario ni la API key en un log.** Los comentarios solo se cuentan (política de 30 días de YouTube) y la key se reemplaza por `***` en cualquier mensaje de error.
- **`logs/cron_errors.log`** junta lo que cualquier trabajo de cron escribió a la salida de errores (por ejemplo, una traza de Python que no alcanzó a llegar a su log). Debe estar vacío.
- **`scripts/status.py`** resume todos los logs en una tabla: último fin de cada trabajo, hace cuánto, si terminó con errores, cuántos ERROR y WARNING en 24 horas, y marca como `ATRASADO` el que no ha corrido cuando le tocaba.

## 3. Pruebas

| Tipo | Qué cubre | Cómo se corre |
|---|---|---|
| **Pruebas unitarias** (67, en `tests/`) | Normalización de títulos, artistas y sellos (incluidos los nombres con coma); validación del CSV del chart; elección del video oficial; mensaje de Kafka sin datos del autor; reintentos de la API ante respuestas cortadas o sin red, sin mostrar la key; versión de consenso de la letra; validación de comentarios y lectura por byte; variables y huella del modelo; métricas de la validación manual; aviso de chart atrasado. Usan casos reales («Suzuki», «BbY WOW», «Tyler, The Creator») y no llaman a ninguna API. | `docker compose exec batch /app/.venv/bin/python -m unittest discover -s tests -t .` (o `uv run python -m unittest discover -s tests -t .` en la Mac) |
| **Conciliación** (`scripts/verify.py`, 12 revisiones) | Que la base cuadre con la carpeta de entrada y con el bronce: archivos, filas por semana, copias con su sha256, canciones y artistas, URI, cuota, privacidad, letras, comentarios, reacción semanal, vistas y capas | `docker compose exec batch /app/.venv/bin/python -m scripts.verify` |
| **Idempotencia** (`scripts/check_idempotency.py`) | Que correr la carga dos veces deje cada tabla idéntica (filas y md5) | `docker compose exec batch /app/.venv/bin/python -m scripts.check_idempotency` |
| **Instalación desde cero** | Que un clon nuevo levante y cree las capas solo | Ver la sección 6 |
| **Validación manual** | Calidad del sentimiento (100 comentarios etiquetados) y del video elegido (20 revisados) | `scripts.label_sentiment` y `scripts.review_videos --revisar` |

## 4. Qué hacer cuando algo falla

Regla general: **casi todo se recupera solo.** Cada trabajo guarda hasta dónde llegó (sha256, marcas de agua, checkpoints) y no avanza esa marca si algo falló. Así, la siguiente corrida repite lo que faltó sin duplicar nada. Lo que sigue es para cuando no se recupera solo, o para saber que no hay que hacer nada.

### La Mac estuvo dormida o apagada

- **Cómo se nota:** `status` marca trabajos como `ATRASADO`; en el dashboard, «Último comentario analizado: hace N h».
- **Qué hacer:** nada, si Docker Desktop está abierto. Al despertar, cada trabajo corre en su siguiente horario y se pone al día: el *poller* pide los comentarios desde su marca de agua y la foto de estadísticas se toma en los siguientes 10 minutos. Si la Mac se apagó, hay que abrir Docker Desktop (o activar *Start Docker Desktop when you sign in*); los servicios arrancan solos (`restart: unless-stopped`).
- **Qué se pierde:** solo el detalle por día de las vistas de los días sin foto; la diferencia queda sumada en la siguiente foto.

### Sin internet o sin DNS

- **Cómo se nota:** en `comments_producer.log`, `ERROR … sin respuesta de la API: URLError: … Name or service not known` y, al tercer video seguido, `Sin conexión a la API en 3 videos seguidos (¿sin internet?)`. Pasó el 23 y el 24 de noviembre de 2026.
- **Qué hacer:** revisar que la Mac tenga internet. Si la Mac sí tiene pero los contenedores no resuelven nombres, reiniciar Docker Desktop. Para probarlo:

    ```bash
    docker compose exec batch /app/.venv/bin/python -c "import socket; print(socket.gethostbyname('www.googleapis.com'))"
    ```

- **Qué pasa mientras:** el ciclo se corta después de 3 videos seguidos sin respuesta, para no gastar intentos. La marca de agua no avanza, así que en el siguiente ciclo se piden los mismos comentarios. No se pierde nada.

### YouTube corta una respuesta a la mitad

- **Cómo se nota:** `NetworkError: sin respuesta de la API: IncompleteRead…` en `comments_producer.log`.
- **Qué hacer:** nada. `youtube_client` reintenta 2 veces con espera (1 y 2 segundos). Si sigue fallando, ese video se salta en este ciclo sin mover su marca de agua, y los demás videos siguen. Antes del 24 de noviembre este error tumbaba el ciclo completo; ya no.

### Se acabó la cuota de YouTube

- **Cómo se nota:** `ERROR … cuota del día agotada (9012/10000; tope propio 9000)` en `video_finder.log` o `comments_producer.log`. En la página de Calidad, la barra del día llega a la línea punteada de 9,000.
- **Qué hacer:** nada. El sistema se impone un tope de 9,000 de las 10,000 unidades; la cuota se reinicia a las 02:00 de la Ciudad de México y los trabajos siguen donde se quedaron. Si pasa todos los días, revisar en `etl_quota_usage` qué método la gasta:

    ```bash
    docker compose exec postgres psql -U admin -d pulso -c "SELECT quota_date, method, units FROM etl_quota_usage ORDER BY quota_date DESC, units DESC LIMIT 10;"
    ```

### La API key no sirve

- **Cómo se nota:** un ERROR que termina en «(revisa YOUTUBE_API_KEY en .env)» en el log del *poller* o de `video_finder`; el ciclo se detiene de inmediato (no tiene caso seguir).
- **Qué hacer:** revisar `YOUTUBE_API_KEY` en `.env` y que la key tenga habilitada la YouTube Data API v3 en Google Cloud Console. Después, `docker compose up -d` para que los contenedores tomen el valor nuevo.

### Un CSV del chart se rechazó

- **Cómo se nota:** `ERROR chart_loader: <archivo> rechazado: <motivo>. No se carga…` en `chart_loader.log`; `status = 'rejected'` en `etl_loaded_file` con el motivo en `detail`. El dashboard sigue mostrando la última semana buena.

    ```bash
    docker compose exec postgres psql -U admin -d pulso -c "SELECT file_name, status, detail FROM etl_loaded_file WHERE status = 'rejected';"
    ```

- **Qué hacer según el motivo:**
  - nombre con una fecha que no es jueves, o archivo incompleto: volver a descargarlo con el botón oficial y dejarlo en `data/landing/charts/`; como el contenido cambia, su sha256 es otro y se carga solo en menos de 15 minutos;
  - Spotify cambió las columnas: ajustar `EXPECTED_HEADER` y el lector en `ingestion/chart_loader.py`, agregar la prueba con el encabezado nuevo, reconstruir (`docker compose up -d --build`) y volver a dejar el archivo.

### Falta descargar el chart de una semana

- **Cómo se nota:** aviso amarillo en la barra lateral del dashboard: «Falta cargar el chart de la semana que terminó el …».
- **Qué hacer:** descargarlo de charts.spotify.com (sección 3 del README) y dejarlo en `data/landing/charts/`. Si se dejan varios a la vez, se cargan de la semana más vieja a la más nueva.

### Se eligió un video que no es el oficial

- **Cómo se nota:** en la revisión manual, o porque los comentarios de una canción no tienen que ver con ella.
- **Qué hacer:**

    ```bash
    docker compose exec batch /app/.venv/bin/python -m scripts.review_videos --set spotify:track:XXXXXXXXXXXXXXXXXXXXXX VIDEO_ID
    ```

  En la siguiente hora, `aggregate_week` descarta los comentarios del video anterior y recalcula sus semanas, y el *poller* empieza a leer el correcto.

### LRCLIB no responde

- **Cómo se nota:** `ERROR lyrics_fetcher: …` y, después de 5 fallas seguidas, `5 fallas seguidas; se detiene y sigue en la siguiente corrida`.
- **Qué hacer:** nada; las canciones que faltaron se buscan en la siguiente hora. Las canciones sin letra quedan fuera de las preguntas de letras y la cobertura se ve en la página de Calidad.

### PostgreSQL o Kafka no están arriba

- **Cómo se nota:** `docker compose ps` no los muestra `healthy`; los trabajos registran `No se pudo conectar a PostgreSQL; se reintenta…`, y el *poller* registra errores de entrega a Kafka sin mover sus marcas de agua.
- **Qué hacer:**

    ```bash
    docker compose logs --tail 50 postgres        # o kafka
    docker compose up -d                          # levanta lo que falte; los datos están en volúmenes
    ```

  Kafka guarda los mensajes 7 días y el consumer sigue desde su último offset confirmado; lo que no llegó a Kafka se vuelve a pedir porque la marca de agua no avanzó.

### Un trabajo falló a la mitad

- **Cómo se nota:** su log tiene `inicio` sin `fin`, o `fin` con `errores=` mayor que 0.
- **Qué hacer:** nada, o correrlo a mano para no esperar a la siguiente hora:

    ```bash
    docker compose exec batch sh -c 'cd /app && /app/.venv/bin/python -m transform.comments_processor'
    ```

  Cada trabajo trabaja por transacciones y guarda su avance solo después del COMMIT: lo que quedó a medias se repite completo y sin duplicados.

### «Otra corrida de … sigue en curso; esta se omite»

- **Qué significa:** cron lanzó un trabajo mientras la corrida anterior seguía (por ejemplo, una carga histórica grande). El candado evita que dos corran a la vez. Es un WARNING, no un error.
- **Qué hacer:** nada, salvo que se repita durante horas. Si es así, revisar el log del trabajo y reiniciar el contenedor: `docker compose restart batch` (el candado se libera al morir el proceso).

### `verify.py` reporta diferencias

| Revisión | Qué significa una diferencia | Qué hacer |
|---|---|---|
| 1–3 (archivos, filas, copias) | Un CSV no se cargó o su copia en el bronce no cuadra | Ver `chart_loader.log`; si el archivo es válido, volver a dejarlo |
| 4–6 (canciones, artistas, URI) | La carga del chart dejó algo inconsistente | Correr `chart_loader` a mano y volver a verificar; si sigue, es un error de código |
| 7 (YouTube) | Un día pasó el tope de cuota, un mensaje trae campos del autor o hay crudo de más de 30 días | Lo último se arregla corriendo `transform.retention`; lo del autor es un error de código que hay que corregir antes de seguir |
| 8 (letras) | Falta el crudo de una letra o la cobertura está debajo de la meta | Esperar a que termine `lyrics_fetcher`; si falta un crudo, `lyrics_processor` regresa la canción a pendiente sola |
| 9 (comentarios) | Hay comentarios en el crudo procesado que no están en la base | Correr `transform.comments_processor`; si persiste, revisar su log |
| 10 (reacción) | Hay parejas canción–semana sin recalcular | Correr `transform.aggregate_week` |
| 11–12 (vistas y capas) | Una vista no existe o una tabla quedó fuera de su capa | Volver a aplicar `db/03_views.sql` (ver sección 6) |

### La imagen de Docker no se construye

- **Cómo se nota:** `docker compose up -d --build` falla en `apt-get` con `Hash Sum mismatch`, o al bajar paquetes de Python.
- **Qué hacer:** volver a intentar. Los paquetes de Debian ya se bajan por HTTPS porque una caché intermedia de la red llegó a entregar archivos alterados por HTTP simple (24 de noviembre). Si falta espacio en disco, `docker image prune` borra las imágenes viejas sin tocar los volúmenes.

### El dashboard no carga

- **Qué hacer:** `docker compose ps` (el servicio `dashboard` debe estar `healthy`) y `docker compose logs --tail 50 dashboard`. Si dice que no puede leer la base, revisar PostgreSQL. El dashboard solo lee, así que reiniciarlo nunca afecta los datos: `docker compose restart dashboard`.

## 5. Respaldo y recuperación

- **El bronce es la primera red de seguridad.** Guarda cada archivo tal como llegó, así que plata y oro se pueden reconstruir sin volver a llamar a las APIs: los CSV del chart están en `data/landing/charts/`, las letras en `raw/lyrics/` y los comentarios de los últimos 29 días en `raw/comments/`.
- **Lo único que no se puede volver a pedir** son las fotos diarias de vistas (`fact_video_daily`): la API solo da el total de hoy. Por eso conviene un respaldo de la base cada semana (por ejemplo, el viernes, al descargar el chart):

    ```bash
    mkdir -p backups
    docker compose exec -T postgres pg_dump -U admin -d pulso -Fc > backups/pulso_$(date +%F).dump
    ```

  `backups/` no se sube al repo. El respaldo trae texto de comentarios, así que también aplica la regla de los 30 días: se borran los respaldos de más de 29 días (`find backups -name '*.dump' -mtime +29 -delete`).
- **Restaurar** (borra lo que haya en la base y pone el respaldo; detener antes los servicios que escriben):

    ```bash
    docker compose stop batch comments_producer consumer dashboard
    docker compose exec -T postgres pg_restore -U admin -d pulso --clean --if-exists < backups/pulso_AAAA-MM-DD.dump
    docker compose start batch comments_producer consumer dashboard
    ```

- **Nunca correr `docker compose down -v` en el sistema real:** borra los volúmenes, incluidas las fotos de vistas. Para probar desde cero, usar otro nombre de proyecto (sección 6).

## 6. Cambios en el esquema y pruebas desde cero

- **Los archivos de `db/` solo corren cuando el volumen de PostgreSQL está vacío.** Para aplicar un cambio de vistas a una base que ya existe:

    ```bash
    docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U admin -d pulso < db/03_views.sql
    ```

  Las tablas usan `CREATE TABLE IF NOT EXISTS`, así que un cambio de columnas en una base existente necesita su `ALTER TABLE`, que se documenta en el README.
- **Probar una instalación desde cero sin tocar el sistema real:** otro nombre de proyecto, que crea sus propios volúmenes, con una API key falsa para no gastar cuota (la variable de la terminal tiene prioridad sobre `.env`):

    ```bash
    YOUTUBE_API_KEY=clave-falsa docker compose -p pulso_prueba up -d --build postgres batch
    # … revisar …
    docker compose -p pulso_prueba down -v        # borra solo los volúmenes de la prueba
    ```

  La prueba escribe en la misma carpeta `logs/`; al terminar conviene revisar los logs con eso en mente.
