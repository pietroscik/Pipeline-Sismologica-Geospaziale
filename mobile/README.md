# Monitoraggio Real-Time Campi Flegrei

Issue #4 - FASE 5.1

Modulo per monitoraggio continuo area Campi Flegrei.

## Struttura

- monitor_campi_flegrei.py - Script principale
- train_risk_model.py - Addestramento modello ML
- config/monitor_config.yaml - Configurazione
- models/ - Directory modelli

## Installazione

1. Addestra modello:
   python mobile/train_risk_model.py

2. Esegui monitor:
   python mobile/monitor_campi_flegrei.py --dry-run

## Esecuzione

Test: python mobile/monitor_campi_flegrei.py --dry-run --once
Prod: python mobile/monitor_campi_flegrei.py

## Servizio (Linux)

Crea /etc/systemd/system/campi-flegrei-monitor.service:

[Unit]
Description=Campi Flegrei Real-Time Monitor
After=network.target

[Service]
User=pietro
WorkingDirectory=/home/pietro/Pipeline-Sismologica-Geospaziale
ExecStart=/home/pietro/venv/bin/python mobile/monitor_campi_flegrei.py
Restart=always
RestartSec=60
Environment=PYTHONUNBUFFERED=1
Environment=ENVIRONMENT=prod

[Install]
WantedBy=multi-user.target

Poi:
- sudo systemctl daemon-reload
- sudo systemctl enable campi-flegrei-monitor
- sudo systemctl start campi-flegrei-monitor

## Dipendenze

pip install obspy pandas numpy scikit-learn joblib
pip install xgboost  # opzionale

## Note

- Stazioni: 24 preconfigurate (minimo 18 richieste)
- Rischio: 0-1 (soglia default: 0.7)
- Dati: FDSN INGV, rete IV
- Output: runs/monitor/
- Log: logs/campi_flegrei_monitor.log