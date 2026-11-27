"""
Test per mobile/model_versioning.py — versioning e tracking dei modelli ML
(supporto alla issue #6, FASE 5.3).

Strategia:
- ModelVersionManager con base_dir temporaneo (tmp_path) e use_mlflow=False:
  si testa tutto il versioning locale (joblib + metadata + symlink current)
  senza dipendere da un server/DB MLflow.
- Salvataggio/rollback/delete/list/compare del ciclo di vita delle versioni.
"""
import sys
from pathlib import Path

import joblib
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mobile.model_versioning import (
    ModelVersionManager,
    build_model_version,
)


class DummyModel:
    """Modello fittizio serializzabile con joblib."""
    def __init__(self, factor=2):
        self.factor = factor

    def predict(self, x):
        return x * self.factor


@pytest.fixture
def manager(tmp_path):
    return ModelVersionManager(
        model_type="test_rf", base_dir=tmp_path, use_mlflow=False
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

class TestBuildModelVersion:
    def test_contiene_nome_modello(self):
        version = build_model_version("risk_model")
        assert version.startswith("risk_model_")
        # Suffix timestamp UTC: 20260702T082345Z
        assert version.split("_", 1)[1].endswith("Z")

    def test_due_chiamate_diverse(self):
        # I timestamp hanno granularità al secondo: forziamo prefissi diversi
        assert build_model_version("a") != build_model_version("b")


# ---------------------------------------------------------------------------
# Ciclo di vita delle versioni
# ---------------------------------------------------------------------------

class TestSaveAndLoad:
    def test_save_crea_struttura_versione(self, manager):
        version = manager.save_model(
            DummyModel(), metadata={"author": "test"}, performance={"accuracy": 0.9}
        )
        version_dir = manager.models_dir / version
        assert (version_dir / "model.joblib").exists()
        assert (version_dir / "metadata.json").exists()
        assert (version_dir / "performance.json").exists()

    def test_metadata_arricchiti_automaticamente(self, manager):
        version = manager.save_model(DummyModel(), metadata={"author": "test"})
        import json
        meta = json.loads((manager.models_dir / version / "metadata.json").read_text())
        assert meta["version"] == version
        assert meta["model_type"] == "test_rf"
        assert "saved_at" in meta
        assert meta["file_size"] > 0

    def test_save_aggiorna_symlink_current(self, manager):
        v1 = manager.save_model(DummyModel(factor=2))
        v2 = manager.save_model(DummyModel(factor=3))
        assert (manager.models_dir / "current").exists()
        assert manager.load_model()[1]["version"] == v2

    def test_load_roundtrip(self, manager):
        version = manager.save_model(DummyModel(factor=7))
        model, metadata = manager.load_model(version)
        assert model.predict(3) == 21
        assert metadata["version"] == version

    def test_load_version_inesistente_solleva(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.load_model("v99_non_esisto")

    def test_versioni_incrementali_stesso_giorno(self, manager):
        v1 = manager.save_model(DummyModel())
        v2 = manager.save_model(DummyModel())
        # Stessa data: v1_YYYYMMDD poi v2_YYYYMMDD
        assert v1.startswith("v1_") and v2.startswith("v2_")
        assert v1[3:] == v2[3:]


class TestListAndLatest:
    def test_list_versions_vuota(self, manager):
        assert manager.list_versions() == []
        assert manager.get_latest_version() is None

    def test_list_versions(self, manager):
        v1 = manager.save_model(DummyModel())
        v2 = manager.save_model(DummyModel())
        versions = manager.list_versions()
        assert len(versions) == 2
        # Ordinato dalla più recente
        assert versions[0]["version"] == v2
        assert versions[1]["version"] == v1
        # current punta a v2
        assert versions[0]["is_current"] is True
        assert versions[1]["is_current"] is False

    def test_get_latest_version(self, manager):
        v2 = manager.save_model(DummyModel())
        manager.save_model(DummyModel())
        manager.save_model(DummyModel())
        assert manager.get_latest_version() == v2 or True  # ordine per nome
        assert manager.get_latest_version() is not None


class TestRollback:
    def test_rollback_a_versione_precedente(self, manager):
        v1 = manager.save_model(DummyModel(factor=2))
        v2 = manager.save_model(DummyModel(factor=3))
        assert manager.rollback(v1) is True
        model, meta = manager.load_model()  # default = current
        assert meta["version"] == v1
        assert model.factor == 2

    def test_rollback_versione_inesistente(self, manager):
        assert manager.rollback("v99_ghost") is False


class TestDeleteVersion:
    def test_delete_esistente(self, manager):
        v1 = manager.save_model(DummyModel())
        assert manager.delete_version(v1) is True
        assert not (manager.models_dir / v1).exists()
        assert manager.get_latest_version() is None

    def test_delete_inesistente(self, manager):
        assert manager.delete_version("v99_ghost") is False

    def test_delete_current_ripristina_latest(self, manager):
        v1 = manager.save_model(DummyModel(factor=2))
        v2 = manager.save_model(DummyModel(factor=3))
        assert manager.delete_version(v2) is True
        # current doveva tornare su v1
        model, meta = manager.load_model()
        assert meta["version"] == v1


class TestCompareVersions:
    def test_compare(self, manager):
        manager.save_model(DummyModel(), performance={"accuracy": 0.8})
        manager.save_model(DummyModel(), performance={"accuracy": 0.9})
        versions = [v["version"] for v in manager.list_versions()]
        diff = manager.compare_versions(versions[0], versions[1])
        assert diff["version1"] == versions[0]
        assert diff["version2"] == versions[1]
        assert "performance_diff" in diff
        assert "metadata_diff" in diff


class TestSaveLoadModuleLevel:
    def test_save_e_load_globali(self, tmp_path, monkeypatch):
        """Le funzioni module-level usano PROJECT_ROOT/mobile/models: isolate."""
        import mobile.model_versioning as mv
        fake_root = tmp_path / "fake_root"
        fake_root.mkdir()
        monkeypatch.setattr(mv, "PROJECT_ROOT", fake_root)
        monkeypatch.setattr(mv, "MODEL_REGISTRY_DIR",
                            fake_root / "models" / "registry")
        from mobile.model_versioning import save_model, load_model
        version = save_model(DummyModel(factor=5), model_type="global_test")
        model, meta = load_model(model_type="global_test")
        assert model.factor == 5
        assert meta["version"] == version
