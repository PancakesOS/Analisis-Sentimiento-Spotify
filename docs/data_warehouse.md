# Almacén de datos por capas: bronce, plata y oro

Este documento describe cómo se organizan los datos del proyecto, qué hay en cada capa, cómo se detectan los cambios de cada fuente y por qué cada carga se puede correr dos veces sin cambiar el resultado. Complementa la sección 7 c del RFC ([rfc.md](rfc.md)) y el README.

## 1. Las capas

```text
Fuentes                 Bronce (zona cruda)              Plata (silver)                 Oro (gold)                   Uso
──────────────────      ─────────────────────────        ───────────────────────        ─────────────────────        ──────────────
CSV del chart  ───────▶ raw/charts/*.csv         ──────▶ dim_song, dim_artist,   ─────▶ song_week_reaction   ─────▶ Dashboard
LRCLIB         ───────▶ raw/lyrics/<uri>.json    ──────▶ song_artist, song_uri,         v_p1 … v_p7                 (Streamlit)
YouTube search ───────▶ raw/search/<uri>.json    ──────▶ dim_date,                      v_business_check
YouTube videos ───────▶ raw/stats/<día>/<hora>   ──────▶ fact_chart_week,               v_model_dataset      ─────▶ Modelo
YouTube coment. ─Kafka▶ raw/comments/<día>.log   ──────▶ fact_comment,                  model_run,                  (scikit-learn)
                                                         fact_video_daily               model_prediction
```

| Capa | Dónde vive | Qué contiene | Quién escribe | Retención |
|---|---|---|---|---|
| **Bronce** | Volumen de Docker `raw` (`data/raw/`), fuera de la base | Cada respuesta tal como llegó: copia del CSV del chart, JSON de LRCLIB, de `search.list` y de `videos.list`, y los comentarios que entrega Kafka (un archivo por día). Nunca se modifica: solo se agregan archivos. | `chart_loader`, `lyrics_fetcher`, `video_finder`, `stats_snapshot`, `consumer` | Comentarios: 29 días (política de YouTube). Lo demás se conserva. |
| **Plata** (`silver`) | Esquema `silver` de PostgreSQL | El modelo dimensional a grano atómico: los datos validados, limpios, deduplicados, con nombres normalizados y una sola identidad por canción, y con el sentimiento y la emoción ya calculados. | `chart_loader`, `lyrics_processor`, `video_finder`, `stats_snapshot`, `comments_processor`, `retention` | `fact_comment`: 29 días. Lo demás se conserva. |
| **Oro** (`gold`) | Esquema `gold` de PostgreSQL | Lo que consume el negocio: la reacción agregada por canción y semana, una vista por pregunta de Laura, el criterio de negocio y los resultados del modelo. | `aggregate_week`, `train`, vistas SQL | Se conserva: es lo único que queda de los comentarios después de 30 días. |
| **Control** (`control`) | Esquema `control` de PostgreSQL | No son datos del negocio: qué archivos ya se cargaron, cuánta cuota se gastó, hasta dónde se leyó cada video y la validación manual. | Todos los trabajos | Se conserva |

**Por qué el bronce está fuera de la base.** Son archivos grandes y heterogéneos (CSV, JSON y líneas de Kafka) que se guardan byte por byte, tal como llegaron. Así se puede reprocesar todo sin volver a llamar a las APIs (la cuota de YouTube es el recurso escaso) y la conciliación compara la base contra el archivo original (`verify.py`, revisiones 3 y 9).

**Por qué el modelo estrella está en plata y no en oro.** Las tablas de hechos guardan un renglón por evento (un comentario, una canción en una semana, un video en un día): es el dato atómico ya limpio, del que se puede sacar cualquier agregado. El oro son los productos terminados que responden una pregunta concreta. El dashboard solo lee de oro.

**Cómo se nombran.** La base busca las tablas en `silver, gold, control` (`ALTER DATABASE … SET search_path`), así que el código las nombra sin esquema. Para ver las capas:

```sql
\dn+                       -- los esquemas, con su descripción
\dt silver.*               -- las tablas de plata
\dv gold.*                 -- las vistas de oro
```

`verify.py` (revisión 12) comprueba que cada tabla y vista esté en su capa y que no quede nada suelto en `public`.

## 2. Bronce: la zona cruda

| Carpeta | Contenido | Nombre del archivo | Escribe |
|---|---|---|---|
| `raw/charts/` | Copia exacta de cada CSV del chart | `regional-mx-weekly-AAAA-MM-DD_<sha256[:12]>.csv` | `chart_loader` |
| `raw/lyrics/` | Las respuestas completas de LRCLIB de una canción (todas las versiones) y las consultas que se hicieron | `<id de la URI>.json` | `lyrics_fetcher` |
| `raw/search/` | La respuesta de `search.list` con la que se eligió el video | `<id de la URI>.json` | `video_finder` |
| `raw/stats/` | Las respuestas de `videos.list` de cada foto | `AAAA-MM-DD/HHMM.json` | `stats_snapshot` |
| `raw/comments/` | Un comentario por línea, como lo entregó Kafka, sin datos del autor | `AAAA-MM-DD.log` (día de llegada) | `consumer` |

Reglas:

- Los archivos se escriben en un temporal y se renombran, así que nunca queda uno a medias.
- Los comentarios llegan por Kafka (tópico `youtube_comments`, retención de 7 días): Kafka es el tránsito hacia el bronce, no una capa.
- El mensaje de Kafka se arma con una lista blanca de campos, así que el bronce nunca tiene el nombre, el canal ni la foto del autor.

## 3. Plata: el modelo dimensional

| Tabla | Grano | Llave | Columnas | Escribe |
|---|---|---|---|---|
| `dim_date` | Un día, de 2005 a 2035 | `date_key` (AAAAMMDD) | `date_key`, `date`, `chart_week_end` (jueves de su semana del chart), `month`, `year` | Se llena al crear la base |
| `dim_artist` | Un artista | `artist_key`; única por `artist_name_norm` | `artist_key`, `artist_name`, `artist_name_norm`, `updated_at` | `chart_loader` |
| `dim_song` | Una canción | `song_key` (sustituta); natural: la URI de Spotify | `song_key`, `spotify_uri`, `track_name`, `track_name_norm`, `main_artist_key`, `label`, `label_norm`, `youtube_video_id`, `video_match`, `video_checked_at`, `lyrics_status`, `lyrics_checked_at`, `lrclib_id`, `lyrics_language`, `lyrics_sentiment`, `lyrics_p_pos`, `lyrics_p_neu`, `lyrics_p_neg`, `lyrics_emotion`, `model_version`, `first_seen_week`, `updated_at` | `chart_loader`, `lyrics_fetcher`, `lyrics_processor`, `video_finder` |
| `song_uri` | Una URI de Spotify | `spotify_uri` | `spotify_uri`, `song_key`, `link_reason` (por qué se unió a esa canción), `linked_at` | `chart_loader` |
| `song_artist` (puente) | Un artista en una canción | (`song_key`, `artist_key`) | `song_key`, `artist_key`, `artist_order` (1 = principal) | `chart_loader` |
| `fact_chart_week` | Una canción en una semana del chart | (`week_key`, `song_key`) | `week_key`, `song_key`, `rank`, `peak_rank`, `previous_rank`, `weeks_on_chart`, `streams` | `chart_loader` |
| `fact_comment` | Un comentario principal | `comment_id` | `comment_id`, `song_key`, `video_id`, `date_key`, `published_at`, `ingested_at`, `text_original`, `like_count`, `reply_count`, `p_pos`, `p_neu`, `p_neg`, `score` (calculada: `p_pos − p_neg`), `sentiment`, `emotion`, `model_version`, `discarded` | `comments_processor`, `aggregate_week` (`discarded`), `retention` |
| `fact_video_daily` | Un video en un día (*snapshot* periódico) | (`date_key`, `song_key`) | `date_key`, `song_key`, `video_id`, `view_count`, `like_count`, `comment_count` (acumulados), `captured_at` | `stats_snapshot` |

Qué pasa de bronce a plata:

- **Validación.** El CSV debe traer el encabezado exacto, `rank` y `uri` sin repetir, tipos y rangos válidos y fecha de jueves; si no, queda `rejected` y no se carga nada de él. Cada comentario se valida (JSON, campos, tipos, fecha) y se descarta con su motivo.
- **Normalización.** Títulos, artistas y sellos se comparan en una forma normalizada (`transform/normalize.py`): minúsculas, sin acentos, sin «(feat. …)». Los artistas se separan por coma, salvo los que llevan coma en el nombre («Tyler, The Creator»).
- **Identidad.** Las URI que Spotify le cambia a una misma canción se unen en una sola (`transform/song_identity.py`).
- **Deduplicación.** `comment_id` es llave primaria; Kafka entrega al menos una vez, así que los repetidos se descartan aquí.
- **Enriquecimiento.** Sentimiento y emoción de cada comentario y de cada letra (pysentimiento, versión fijada), la versión de consenso de la letra y el video oficial.
- **Exclusión.** Los comentarios de un video que resultó no ser el oficial, o de una canción con letra en inglés, quedan con `discarded = true`.

## 4. Oro: lo que consume el negocio

| Objeto | Qué es | Sale de |
|---|---|---|
| `song_week_reaction` (tabla) | Reacción del público por canción y semana: `n_comments`, `pct_pos`, `pct_neu`, `pct_neg`, `avg_score`, `dominant_emotion`; más `last_ingested_at` para saber qué recalcular | `fact_comment` (no descartados) |
| `model_run` (tabla) | Un entrenamiento: huella de los datos, semanas de entrenamiento y prueba, métricas contra el baseline y coeficientes | `v_model_dataset` |
| `model_prediction` (tabla) | Probabilidad de subir de cada canción del top 50 de la semana más reciente | `v_model_dataset` y el modelo |
| `v_chart_week`, `v_label`, `v_song`, `v_video_week`, `v_song_longevity` | Vistas de apoyo: semanas cargadas, nombre del sello, canción con su letra (solo letras en español), vistas ganadas por semana y semanas en el chart | Plata |
| `v_model_dataset` | Una fila por canción del top 50 y semana, con la etiqueta `went_up` | Plata y `song_week_reaction` |
| `v_p1` … `v_p7` | Una vista por pregunta de negocio (RFC, sección 7 b) | Plata, `song_week_reaction` y las vistas de apoyo |
| `v_business_check` | Criterio de negocio: cuántas de las candidatas del resumen suben la semana siguiente | `v_p1` y `v_model_dataset` |

## 5. Control

| Tabla | Para qué |
|---|---|
| `etl_loaded_file` | Cada CSV del chart que se vio: sha256, nombre, semana, filas, ruta en el bronce, `status` (`loaded`, `replaced` o `rejected`) y motivo |
| `etl_quota_usage` | Unidades de cuota de YouTube por día y método |
| `video_watermark` | Por video: el comentario más reciente ya leído, el avance de la carga histórica y si tiene los comentarios desactivados |
| `sentiment_label`, `video_check` | Validación manual (criterios de éxito del RFC). `sentiment_label` no guarda texto. |

Además, fuera de la base, `data/state/comments_processor.json` guarda hasta qué byte de cada archivo del bronce llegó `comments_processor`.

## 6. Cómo se detectan los cambios

Cada fuente cambia de forma distinta. La regla general es la misma: **cada trabajo sabe qué ya procesó y solo toma lo nuevo o lo que cambió**, con una marca que guarda en la base o en el estado, nunca por fecha de corrida.

| Qué cambia | Cómo se detecta | Qué se hace |
|---|---|---|
| Llega un CSV nuevo del chart | Su sha256 no está en `etl_loaded_file` | Se valida y se carga en una transacción |
| Llega el mismo CSV otra vez, aunque tenga otro nombre | Su sha256 ya está en `etl_loaded_file` | Se omite |
| Llega otro CSV de una semana ya cargada (Spotify corrigió el chart) | Otro sha256 con la misma `chart_week_end` | Se reemplaza la semana completa (DELETE + INSERT en una transacción); el archivo anterior queda `replaced` |
| Cambia el título, el sello o los artistas de una canción | Al cargar, se compara contra `dim_song` | SCD tipo 1: se sobrescribe y se actualiza `updated_at`, **solo si el archivo es de una semana igual o posterior** a la última en que apareció la canción. Un archivo viejo cargado tarde no pisa datos nuevos. |
| Spotify le cambia la URI a una canción | La URI no está en `song_uri`, pero el título y el artista normalizados coinciden y su `previous_rank` o `weeks_on_chart` la llevan a una canción existente | Se une a esa canción (`song_uri.link_reason`); su historia no se parte |
| Comentarios nuevos en un video | `published_at` mayor que `video_watermark.last_published_at` | El *poller* pide solo esas páginas; la marca avanza después de `kafka.flush()` |
| Líneas nuevas en el bronce de comentarios | Byte guardado en `state/comments_processor.json` | Se leen solo los bytes nuevos; el checkpoint se guarda después del COMMIT |
| Comentarios nuevos en una pareja canción–semana | `max(fact_comment.ingested_at) > song_week_reaction.last_ingested_at` | Se recalcula solo esa pareja |
| Se corrige el video oficial de una canción | `fact_comment.video_id` distinto de `dim_song.youtube_video_id` | `aggregate_week` marca esos comentarios como descartados y recalcula sus semanas; el *poller* empieza a leer el video nuevo |
| Una letra queda en inglés | `dim_song.lyrics_language = 'en'` | Sus comentarios se descartan y el *poller* deja de leer su video |
| Una letra no estaba en LRCLIB | `lyrics_status = 'not_found'` y `lyrics_checked_at` de hace más de 7 días | Se vuelve a buscar |
| Se elige otra versión de la letra | Cambia `lrclib_id` | Se borra su sentimiento y `lyrics_processor` lo recalcula |
| Cambia el modelo de sentimiento | `model_version` de la canción distinta a la versión actual | `lyrics_processor` recalcula todas las letras con la versión nueva |
| Cambian las vistas de un video | La foto del día ya existe | Upsert por (`date_key`, `song_key`): queda la última foto del día |
| Cambian los datos del modelo | La huella md5 de los datos y la configuración no está en `model_run` | Se reentrena; si es la misma huella, no |
| Cambia una predicción | La probabilidad o el modelo difieren de lo guardado | Solo esa fila se actualiza |
| Pasan 29 días desde que llegó un comentario | `ingested_at` y la fecha del archivo del bronce | `retention` borra la fila y el archivo |

Lo que no se detecta, a propósito: si alguien **edita** un comentario después de que se leyó, se conserva la versión original (`ON CONFLICT DO NOTHING`). Releer todos los comentarios para ver ediciones costaría cuota todos los días y no cambia la reacción semanal de forma apreciable.

## 7. Carga idempotente

Correr un trabajo dos veces seguidas deja exactamente lo mismo. Cada trabajo lo garantiza con un mecanismo, y ninguno depende de la hora a la que corre:

| Trabajo | Capa | Por qué correrlo dos veces no cambia nada |
|---|---|---|
| `chart_loader` | bronce → plata | El sha256 del archivo ya está registrado; un archivo, una transacción |
| `lyrics_fetcher` | fuente → bronce | Solo busca canciones sin estado (o `not_found` de hace más de 7 días) |
| `lyrics_processor` | bronce → plata | Solo procesa letras con `model_version` distinta a la actual; una canción, una transacción |
| `video_finder` | fuente → bronce → plata | Solo busca canciones sin video que no se buscaron en los últimos 30 días (`video_checked_at`) |
| `stats_snapshot` | fuente → bronce → plata | Upsert por (`date_key`, `song_key`) |
| `comments_producer` + `consumer` | fuente → bronce | La marca de agua avanza solo después de entregar a Kafka; si algo se repite, se elimina en la plata por `comment_id` |
| `comments_processor` | bronce → plata | Checkpoint por byte guardado después del COMMIT; `INSERT … ON CONFLICT (comment_id) DO NOTHING` |
| `aggregate_week` | plata → oro | Solo recalcula parejas con comentarios posteriores a `last_ingested_at`; DELETE + INSERT de esas parejas en una transacción |
| `retention` | plata y bronce | Si no hay nada de 29 días o más, no borra nada |
| `train` | oro → oro | Misma huella de datos, mismo modelo; las predicciones solo se tocan si cambian |

Además, cada trabajo toma un candado (`flock`) al empezar: si cron lo lanza mientras otra corrida sigue en curso, la segunda se omite y lo anota en su log.

**Cómo comprobarlo:**

```bash
docker compose exec batch /app/.venv/bin/python -m scripts.check_idempotency
```

Corre dos veces seguidas todos los trabajos que leen del bronce y escriben en plata y oro, y compara el número de filas y una huella md5 de todas las filas de cada tabla. Resultado del 24 de noviembre de 2026:

```text
Tabla                                Filas  Huella (md5)                      Resultado
control.etl_loaded_file                 54  55bf76047f0158cda9ee91ea16486994  igual
gold.model_prediction                   41  3f292f9de62bf645ea385d73cb3d3fc0  igual
gold.model_run                           6  ec88a88420f2304fa3e8bd9383d7c082  igual
gold.song_week_reaction              2,577  5dd553bf46d9ba691ee201952d7504a9  igual
silver.dim_artist                      358  2e5c3e4f9235cb498f025dd2824b8f77  igual
silver.dim_date                     11,322  a67a9c171e86a4efb19611b3d3d6d0f6  igual
silver.dim_song                        663  8194bc611a3a9467807f0414a37610bb  igual
silver.fact_chart_week              10,799  cbe39be66e857530a959cf9cec4b94f7  igual
silver.fact_comment                741,390  4c4377a8cd6df97953e56306118e5aae  igual
silver.fact_video_daily                307  f97380c81366a64e451bde108e4ca8a9  igual
silver.song_artist                   1,021  61546f5bdde7e11191a22cda0eacee6b  igual
silver.song_uri                        716  8d46825d1e6de93534b15fda0c60155b  igual

OK
```

La primera vez que se corrió, esta prueba encontró que `train` reescribía las predicciones en cada corrida (cambiaba `predicted_at` aunque la probabilidad fuera la misma). Se corrigió con un upsert que solo toca las filas que cambian.

Los trabajos que llaman a APIs externas no entran en la prueba, porque una segunda llamada puede traer datos nuevos de verdad. Su idempotencia está en su estado: no repiten lo que ya hicieron.

La carga también es reproducible en el otro sentido: el chart se cargó de la semana más nueva a la más vieja y el resultado fue idéntico, fila por fila, al de cargarlo en orden (README, «Decisiones que se tomaron al construir»).
