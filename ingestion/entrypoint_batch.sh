#!/bin/sh
# Arranque del contenedor batch.
#
# 1. cron no hereda las variables de entorno del contenedor: se copian a
#    /etc/environment para que los trabajos las vean.
# 2. Corre una vez la carga del chart, la búsqueda de videos y la foto de
#    estadísticas, para no esperar a su horario. Si algo falla, cron lo
#    vuelve a intentar.
# 3. Deja cron en primer plano como proceso principal del contenedor.

printenv | grep -E '^(POSTGRES_|YOUTUBE_|KAFKA_|TZ=)' > /etc/environment

cd /app
for job in ingestion.chart_loader ingestion.video_finder ingestion.stats_snapshot; do
    /app/.venv/bin/python -m "$job" || echo "$job terminó con errores; ver logs/"
done

exec cron -f
