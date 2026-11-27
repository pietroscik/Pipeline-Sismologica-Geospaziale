"""
Test di validazione del deployment Docker del retraining (issue #7).

Verifica che:
- la config operativa (mobile/config/retraining_config.yaml) sia valida per
  l'orchestratore e coerente con la schedulazione del container
- docker-compose.yml dichiari il servizio "retraining" con i volumi attesi
- il crontab del container e l'entrypoint abbiano formato e contenuto attesi
"""
import importlib.util
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mobile.retrain_models import RetrainingConfig

RETRAINING_CONFIG = PROJECT_ROOT / "mobile" / "config" / "retraining_config.yaml"
COMPOSE = PROJECT_ROOT / "docker-compose.yml"
CRONTAB = PROJECT_ROOT / "scripts" / "docker" / "retrain-crontab"
ENTRYPOINT = PROJECT_ROOT / "scripts" / "docker" / "scheduler-entrypoint.sh"


class TestConfigOperativa:
    def test_config_relativa_valida(self):
        """La config del repo si carica con l'orchestratore senza errori."""
        cfg = RetrainingConfig(RETRAINING_CONFIG)
        assert cfg.get("retraining", "enabled") is True
        assert cfg.get("retraining", "models_to_retrain")  # non vuota
        assert cfg.get("retraining", "performance_threshold", default=0) > 0
        assert cfg.get("notification", "enabled") is True

    def test_soglia_deploy_realistica(self):
        """0.85 era troppo severo per dati sismici: la config operativa lo abbassa."""
        cfg = RetrainingConfig(RETRAINING_CONFIG)
        assert cfg.get("retraining", "performance_threshold") <= 0.75

    def test_schedule_coerente_con_cron(self):
        """schedule 'monthly' nel config = interval_seconds ~30 giorni."""
        cfg = RetrainingConfig(RETRAINING_CONFIG)
        assert cfg.get("retraining", "schedule") == "monthly"
        assert cfg.get("retraining", "interval_seconds") == 2592000  # 30 giorni

    def test_canali_notifica_supportati(self):
        """FallbackAlertSystem supporta console/file: niente canali non implementati."""
        cfg = RetrainingConfig(RETRAINING_CONFIG)
        channels = cfg.get("notification", "channels")
        assert set(channels) <= {"console", "file"}


class TestDockerCompose:
    def test_yaml_valido(self):
        data = yaml.safe_load(COMPOSE.read_text())
        assert "services" in data

    def test_servizio_retraining_presente(self):
        data = yaml.safe_load(COMPOSE.read_text())
        svc = data["services"].get("retraining")
        assert svc is not None, "il servizio retraining manca in docker-compose.yml"
        assert svc["build"]["dockerfile"] == "Dockerfile"
        assert svc["restart"] == "unless-stopped"

    def test_volume_codice_montato(self):
        """Il codice è montato: modelli e run persistono sull'host."""
        data = yaml.safe_load(COMPOSE.read_text())
        volumes = data["services"]["retraining"]["volumes"]
        assert ".:/app" in volumes

    def test_postgis_invariato(self):
        """Il servizio db preesistente non è stato alterato."""
        data = yaml.safe_load(COMPOSE.read_text())
        assert data["services"]["db_geospaziale"]["image"] == "postgis/postgis:15-3.3"
        assert "postgis_data" in data["volumes"]


class TestSchedulerFiles:
    def test_crontab_formato(self):
        """Il crontab ha 5 campi di tempo + utente + comando (formato /etc/cron.d)."""
        lines = [
            ln.strip() for ln in CRONTAB.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        assert len(lines) == 1
        fields = lines[0].split()
        assert fields[:6] == ["0", "3", "1", "*", "*", "root"]
        assert "mobile.retrain_models" in lines[0]

    def test_crontab_comando_logga_su_file(self):
        lines = [
            ln.strip() for ln in CRONTAB.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        assert "retraining_cron.log" in lines[0]

    def test_entrypoint_eseguibile_e_contenuto(self):
        content = ENTRYPOINT.read_text()
        assert content.startswith("#!/bin/sh")
        assert "cron -f" in content  # cron in foreground (PID 1)
        assert "mkdir -p /app/logs" in content

    def test_dockerfile_copia_scheduler(self):
        content = (PROJECT_ROOT / "Dockerfile").read_text()
        assert "scripts/docker/retrain-crontab" in content
        assert "scheduler-entrypoint.sh" in content
        assert "scikit-learn" in content  # dipendenza dell'orchestratore

    def test_entrypoint_permessi(self):
        """Il file deve essere eseguibile per l'utente corrente."""
        assert ENTRYPOINT.stat().st_mode & 0o111, "entrypoint non eseguibile"
