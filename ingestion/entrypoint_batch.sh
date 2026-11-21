#!/bin/sh
# Arranque del contenedor batch.
#
# 1. cron no hereda las variables de entorno del contenedor: se copian a
#    /etc/environment para que los trabajos las vean.
# 2. Corre chart_loader una vez, para no esperar 15 minutos a que cargue
#    los CSV que ya estaban en la carpeta de entrada. Si falla, cron lo
#    vuelve a intentar.
# 3. Deja cron en primer plano como proceso principal del contenedor.

printenv | grep -E '^(POSTGRES_|YOUTUBE_|KAFKA_|TZ=)' > /etc/environment

cd /app
/app/.venv/bin/python -m ingestion.chart_loader || echo "chart_loader terminó con errores; ver logs/chart_loader.log"

exec cron -f
