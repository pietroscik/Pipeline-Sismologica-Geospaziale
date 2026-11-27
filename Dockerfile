# Immagine per il servizio di retraining automatico (issue #7).
# Dipendenze minime: l'orchestratore (mobile/retrain_models.py) usa solo
# pandas/numpy/scikit-learn/joblib/yaml. Il codice del progetto è montato
# come volume (vedi docker-compose.yml), così modelli e run restano sull'host.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron rsyslog \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dipendenze Python minime per il retraining
RUN pip install --no-cache-dir \
    "pandas>=1.3" \
    "numpy>=1.20" \
    "scikit-learn>=1.0" \
    "joblib>=1.2" \
    "pyyaml>=5.4" \
    "python-json-logger"

COPY scripts/docker/retrain-crontab /etc/cron.d/retrain
COPY scripts/docker/scheduler-entrypoint.sh /usr/local/bin/scheduler-entrypoint.sh
RUN chmod 0644 /etc/cron.d/retrain \
    && chmod +x /usr/local/bin/scheduler-entrypoint.sh

# Il codice è montato come volume: immagine stabile, dati sull'host
VOLUME ["/app"]

ENTRYPOINT ["scheduler-entrypoint.sh"]
