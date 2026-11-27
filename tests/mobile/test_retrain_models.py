"""
Test per mobile/retrain_models.py — retraining automatico modelli ML (issue #7).

Strategia:
- RetrainingConfig con file di config temporanei (deep merge, default, get).
- DataLoader con dataset sintetico in una cwd temporanea (i path nel config
  sono relativi alla working directory: contratto da documentare nei test).
- Orchestratore: trigger temporale/dati/prestazioni, retrain end-to-end con
  dataset lineare (deploy garantito), failure path, summary di retrain_all.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mobile.retrain_models import (
    DataLoader,
    FallbackAlertSystem,
    FallbackModelVersionManager,
    ModelEvaluator,
    ModelTrainer,
    RetrainingConfig,
    RetrainingOrchestrator,
)


def make_synthetic_csv(path, n=200):
    """Dataset lineare y = 2*x1 + x2: R2 ~ 1 garantito (deploy true)."""
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "x1": rng.normal(size=n),
        "x2": rng.normal(size=n),
    })
    df["target"] = 2 * df["x1"] + df["x2"]
    df.to_csv(path, index=False)
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Cwd temporanea con dataset sintetico e config di retraining."""
    (tmp_path / "data").mkdir()
    make_synthetic_csv(tmp_path / "data" / "dataset.csv")
    config = {
        "retraining": {
            "enabled": True,
            "interval_seconds": 999999999,   # trigger temporale spento
            "min_new_samples": 10_000,       # trigger dati spento
            "performance_threshold": 0.5,
            "models_to_retrain": ["random_forest"],
            "cross_validation_folds": 3,
            "test_size": 0.25,
        },
        "data": {"input_dir": str(tmp_path / "data"), "input_file": "dataset.csv", "min_samples": 10},
        "notification": {"enabled": True, "success": True, "failure": True},
    }
    cfg_path = tmp_path / "retraining_config.yaml"
    cfg_path.write_text(yaml.dump(config))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# RetrainingConfig
# ---------------------------------------------------------------------------

class TestRetrainingConfig:
    def test_default_config(self, tmp_path):
        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        assert cfg.get("retraining", "enabled") is True
        assert cfg.get("retraining", "models_to_retrain") == ["xgboost", "random_forest"]
        assert cfg.get("retraining", "interval_seconds") == 604800

    def test_get_con_default(self, tmp_path):
        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        assert cfg.get("chiave", "inesistente", default="fallback") == "fallback"

    def test_deep_merge_con_file(self, tmp_path):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text(yaml.dump({
            "retraining": {"enabled": False},  # override di una sola chiave
        }))
        cfg = RetrainingConfig(cfg_file)
        assert cfg.get("retraining", "enabled") is False
        # Le altre chiavi del default sopravvivono al merge
        assert cfg.get("retraining", "interval_seconds") == 604800

    def test_yaml_invalido_mantiene_default(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : : non[valido")
        cfg = RetrainingConfig(bad)
        assert cfg.get("retraining", "enabled") is True


# ---------------------------------------------------------------------------
# DataLoader, ModelTrainer, ModelEvaluator
# ---------------------------------------------------------------------------

class TestDataLoader:
    def test_load_suddivide_feature_e_target(self, workspace):
        cfg = RetrainingConfig(workspace / "retraining_config.yaml")
        X, y = DataLoader(cfg).load_data()
        assert list(X.columns) == ["x1", "x2"]       # tutte tranne l'ultima
        assert y.name == "target"                    # ultima colonna = target
        assert len(X) == len(y) == 200

    def test_file_mancante_solleva(self, tmp_path):
        # input_dir puntato su una directory inesistente (assoluta)
        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        cfg.config["data"]["input_dir"] = str(tmp_path / "non_esiste")
        with pytest.raises(FileNotFoundError):
            DataLoader(cfg).load_data()


class TestModelTrainer:
    def test_train_random_forest(self, workspace):
        cfg = RetrainingConfig(workspace / "retraining_config.yaml")
        trainer = ModelTrainer(cfg)
        model = trainer.train("random_forest", np.random.rand(50, 3),
                               np.random.rand(50))
        assert hasattr(model, "predict")

    def test_train_modello_sconosciuto_solleva(self, workspace):
        cfg = RetrainingConfig(workspace / "retraining_config.yaml")
        with pytest.raises(ValueError, match="Unknown model type"):
            ModelTrainer(cfg).train("svm", np.random.rand(10, 2), np.random.rand(10))

    def test_cross_validate(self, workspace):
        cfg = RetrainingConfig(workspace / "retraining_config.yaml")
        X = np.random.rand(60, 3)
        y = np.random.rand(60)
        cv = ModelTrainer(cfg).cross_validate(
            __import__("sklearn.ensemble", fromlist=["RandomForestRegressor"])
            .RandomForestRegressor(), X, y)
        assert len(cv["cv_scores"]) == 3  # cross_validation_folds dal config
        assert "mean_cv_score" in cv and "std_cv_score" in cv


class TestModelEvaluator:
    def test_evaluate_metriche(self, tmp_path):
        from sklearn.ensemble import RandomForestRegressor

        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        X = np.random.rand(100, 3)
        y = np.random.rand(100)
        model = RandomForestRegressor().fit(X, y)
        metrics = ModelEvaluator(cfg).evaluate(model, X, y)
        for key in ("r2", "mse", "rmse", "mae"):
            assert key in metrics
        # Sul training set R2 è alto per un RF
        assert metrics["r2"] > 0.5

    def test_should_deploy(self, tmp_path):
        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        ev = ModelEvaluator(cfg)
        assert ev.should_deploy({"r2": 0.9}, threshold=0.85) is True
        assert ev.should_deploy({"r2": 0.5}, threshold=0.85) is False
        assert ev.should_deploy({}, threshold=0.85) is False  # senza r2


# ---------------------------------------------------------------------------
# FallbackModelVersionManager / FallbackAlertSystem
# ---------------------------------------------------------------------------

class TestFallbackVersionManager:
    def test_prima_versione(self, tmp_path):
        mgr = FallbackModelVersionManager(tmp_path / "models")
        assert mgr.get_latest_version("rf") is None
        assert mgr.get_next_version("rf") == "v1.0.0"

    def test_incremento_versione(self, tmp_path):
        mgr = FallbackModelVersionManager(tmp_path / "models")
        assert mgr.get_next_version("rf") == "v1.0.0"
        (mgr.models_dir / "rf" / "v1.0.0").mkdir(parents=True)
        assert mgr.get_next_version("rf") == "v1.0.1"

    def test_save_e_load_roundtrip(self, tmp_path):
        from sklearn.ensemble import RandomForestRegressor

        mgr = FallbackModelVersionManager(tmp_path / "models")
        model = RandomForestRegressor().fit(np.random.rand(20, 2), np.random.rand(20))
        path = mgr.save_model(model, "rf", "v1.0.0", {"r2": 0.9})
        assert path.exists()
        metrics = json.loads((mgr.models_dir / "rf" / "v1.0.0" / "metrics.json").read_text())
        assert metrics["r2"] == 0.9
        loaded = mgr.load_model("rf", "v1.0.0")
        assert hasattr(loaded, "predict")


class TestFallbackAlertSystem:
    def test_notifica_successo_rispetta_config(self, tmp_path):
        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        cfg.config["notification"]["success"] = False
        alert = FallbackAlertSystem(cfg)
        alert.send_alert("msg", "success")  # disattivato: non deve sollevare

    def test_notifica_failure_disattivata(self, tmp_path):
        cfg = RetrainingConfig(tmp_path / "non_esiste.yaml")
        cfg.config["notification"]["failure"] = False
        FallbackAlertSystem(cfg).send_alert("msg", "failure")


# ---------------------------------------------------------------------------
# Orchestratore: trigger
# ---------------------------------------------------------------------------

class TestTriggers:
    def test_trigger_temporale_prima_esecuzione(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        # Nessun last_run.txt: la prima esecuzione deve triggerare
        assert orch._check_temporal_trigger() is True

    def test_trigger_temporale_spento_con_last_run_recente(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        (orch.runs_dir / "last_run.txt").write_text(datetime.now().isoformat())
        # interval_seconds = 999999999 nel config di test: troppo lontano
        assert orch._check_temporal_trigger() is False

    def test_check_triggers_disabled(self, workspace):
        cfg_file = workspace / "retraining_config.yaml"
        config = yaml.safe_load(cfg_file.read_text())
        config["retraining"]["enabled"] = False
        cfg_file.write_text(yaml.dump(config))
        orch = RetrainingOrchestrator(RetrainingConfig(cfg_file))
        assert orch.check_triggers() == []

    def test_trigger_dati_con_crescita(self, workspace):
        cfg_file = workspace / "retraining_config.yaml"
        config = yaml.safe_load(cfg_file.read_text())
        config["retraining"]["min_new_samples"] = 50  # 200 campioni nuovi > 50
        cfg_file.write_text(yaml.dump(config))
        orch = RetrainingOrchestrator(RetrainingConfig(cfg_file))
        assert orch._check_data_trigger(50) is True
        # Il conteggio è stato salvato: alla seconda chiamata (0 nuovi) non triggera
        assert orch._check_data_trigger(50) is False

    def test_trigger_prestazioni_con_metriche_basse(self, workspace):
        """Dopo il fix: se l'ultimo modello ha r2 sotto soglia, il trigger scatta."""
        cfg = RetrainingConfig(workspace / "retraining_config.yaml")
        orch = RetrainingOrchestrator(cfg)
        # Simula un modello salvato con r2 scarso
        mgr = orch.model_version_manager
        version_dir = mgr.models_dir / "random_forest" / "v1.0.0"
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "metrics.json").write_text(json.dumps({"r2": 0.1}))
        assert orch._check_performance_trigger() is True

    def test_trigger_prestazioni_silente_senza_modelli(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        # Nessun modello salvato: nessun crash, nessun trigger
        assert orch._check_performance_trigger() is False


# ---------------------------------------------------------------------------
# Orchestratore: retrain end-to-end
# ---------------------------------------------------------------------------

class TestRetrainModel:
    def test_retrain_deploy(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        result = orch.retrain_model("random_forest")
        assert result["status"] == "success"
        # Dataset lineare + soglia 0.5: il deploy deve avvenire
        assert result["deployed"] is True
        # Convenzione del versioning reale: v1_YYYYMMDD
        assert result["new_version"].startswith("v1_")
        assert result["metrics"]["r2"] > 0.5
        # Il modello è effettivamente salvato (con l'interfaccia dell'adapter)
        version = result["new_version"]
        assert (orch.model_version_manager.models_dir / "random_forest"
                / version / "model.joblib").exists()
        # metrics.json (trigger retraining) e performance.json (monitor) entrambi presenti
        vdir = orch.model_version_manager.models_dir / "random_forest" / version
        assert (vdir / "metrics.json").exists()
        assert (vdir / "performance.json").exists()
        # Il symlink 'current' punta alla nuova versione: il monitor può caricarla
        assert (vdir.parent / "current").exists()
        # Il report JSON e last_run.txt sono scritti
        assert (orch.runs_dir / "last_run.txt").exists()
        reports = list(orch.runs_dir.glob("retraining_random_forest_*.json"))
        assert len(reports) == 1
        assert json.loads(reports[0].read_text())["status"] == "success"

    def test_retrain_failure_gestita(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        # input_file inesistente → failure registrata, non eccezione
        orch.config.config["data"]["input_file"] = "manca.csv"
        result = orch.retrain_model("random_forest")
        assert result["status"] == "failure"
        assert "error" in result
        # Il report di failure è comunque scritto
        assert list(orch.runs_dir.glob("retraining_random_forest_*.json"))

    def test_retrain_all_summary(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        overall = orch.retrain_all(["random_forest", "tipo_inesistente"])
        assert overall["summary"]["total"] == 2
        assert overall["summary"]["success"] >= 1
        assert overall["summary"]["failure"] >= 1
        assert "random_forest" in overall["models"]


class TestVersioningAdapterIntegration:
    """Test di integrazione: retraining -> versioning reale -> monitor.

    Chiude il cerchio rotto prima dell'adapter: i modelli salvati dal
    retraining devono essere caricabili dal monitor tramite il symlink
    'current' del sistema di versioning reale.
    """

    def test_retrain_deploy_poi_monitor_carica_current(self, workspace):
        orch = RetrainingOrchestrator(RetrainingConfig(workspace / "retraining_config.yaml"))
        result = orch.retrain_model("random_forest")
        assert result["status"] == "success" and result["deployed"] is True

        # Il monitor carica con l'API reale del versioning (via 'current')
        from mobile.model_versioning import ModelVersionManager

        manager = ModelVersionManager(
            model_type="random_forest",
            base_dir=orch.model_version_manager.models_dir,
            use_mlflow=False,
        )
        model, metadata = manager.load_model("current")
        assert hasattr(model, "predict")
        assert metadata["version"] == result["new_version"]
        assert metadata["trained_by"] == "retraining"

        # Le performance lette dal monitor coincidono con le metriche del retraining
        import json as _json
        perf = _json.loads(
            (manager.models_dir / result["new_version"] / "performance.json").read_text()
        )
        assert abs(perf["r2"] - result["metrics"]["r2"]) < 1e-9

    def test_adapter_interfaccia(self, tmp_path):
        """L'adapter espone esattamente il contratto usato dall'orchestratore."""
        from mobile.retrain_models import ModelVersioningAdapter

        adapter = ModelVersioningAdapter(models_dir=tmp_path / "models")
        assert adapter.get_latest_version("xgb") is None
        # Prima versione con la convenzione del manager reale
        assert adapter.get_next_version("xgb").startswith("v1_")

        from sklearn.ensemble import RandomForestRegressor

        model = RandomForestRegressor().fit(np.random.rand(20, 2), np.random.rand(20))
        version = adapter.get_next_version("xgb")
        path = adapter.save_model(model, "xgb", version, {"r2": 0.9})
        assert path.exists()
        assert adapter.get_latest_version("xgb") == version
        loaded = adapter.load_model("xgb", version)
        assert hasattr(loaded, "predict")
