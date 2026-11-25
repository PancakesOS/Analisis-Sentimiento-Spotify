#!/bin/sh
# Arranque del contenedor batch.
#
# 1. cron no hereda las variables de entorno del contenedor: se copian a
#    /etc/environment para que los trabajos las vean.
# 2. Carga los CSV del chart antes que nada: todo lo demás depende de él.
# 3. En segundo plano corre una vez el resto de los trabajos, en el orden en
#    que dependen uno de otro, para no esperar a su horario (en un clon
#    nuevo, el dashboard tiene datos en minutos y no hasta la siguiente
#    hora). Cada trabajo tiene su candado: si cron lo lanza mientras tanto,
#    esa corrida se omite. Si algo falla, cron lo vuelve a intentar.
# 4. Deja cron en primer plano como proceso principal del contenedor.

printenv | grep -E '^(POSTGRES_|YOUTUBE_|KAFKA_|TZ=|HF_|TRANSFORMERS_)' > /etc/environment

cd /app
PY=/app/.venv/bin/python

$PY -m ingestion.chart_loader || echo "chart_loader terminó con errores; ver logs/"

(
    for job in ingestion.video_finder ingestion.stats_snapshot ingestion.lyrics_fetcher \
               transform.lyrics_processor transform.comments_processor transform.aggregate_week \
               delivery.model.train; do
        $PY -m "$job" > /dev/null 2>> /app/logs/cron_errors.log \
            || echo "$job terminó con errores; ver logs/" >> /app/logs/cron_errors.log
    done
) &

exec cron -f
