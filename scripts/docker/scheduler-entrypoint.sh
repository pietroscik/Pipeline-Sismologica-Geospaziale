#!/bin/sh
# Entry point del servizio scheduler di retraining (issue #7).
# Avvia cron in primo piano: i job sono definiti in /etc/cron.d/retrain.
set -eu

mkdir -p /app/logs /app/runs

echo "[scheduler] Avvio cron per il retraining automatico..."
echo "[scheduler] Job attivo: retraining mensile (giorno 1, 03:00) - vedi /etc/cron.d/retrain"

# Stampa il crontab all'avvio per logging
cat /etc/cron.d/retrain

# cron in foreground (delegation via PID 1); rsyslogd per il log di sistema
rsyslogd
exec cron -f
