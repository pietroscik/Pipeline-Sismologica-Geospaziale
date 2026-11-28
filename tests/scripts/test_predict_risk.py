"""
Test per scripts/predict_risk.py: inferenza dal bundle .pkl standardizzato
(model + scaler + feature_names + threshold + lookback) che azzera il
train-serve skew tra training e produzione.
"""

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from predict_risk import load_artifact, predict_proba_bundle  # noqa: E402


class _FakeTabularModel:
    """Modello sklearn-like minimale con predict_proba."""

    def predict_proba(self, X):
        proba = np.clip(X.mean(axis=1), 0.0, 1.0)
        return np.column_stack([1 - proba, proba])


def _make_artifact(tmp_path, model_type="random_forest", scaler=None,
                   feature_names=("f1", "f2"), threshold=0.5, lookback=None,
                   as_dict=True):
    artifact = {
        "model_type": model_type,
        "model": _FakeTabularModel(),
        "scaler": scaler,
        "feature_names": list(feature_names),
        "threshold": threshold,
        "lookback": lookback,
    }
    path = tmp_path / "bundle.pkl"
    if as_dict:
        joblib.dump(artifact, path)
    else:
        joblib.dump(_FakeTabularModel(), path)  # modello nudo, non bundle
    return artifact, path


class TestLoadArtifact:
    def test_bundle_valido(self, tmp_path):
        artifact, path = _make_artifact(tmp_path)
        loaded = load_artifact(path)
        assert loaded["model_type"] == "random_forest"
        assert loaded["threshold"] == 0.5
        assert loaded["feature_names"] == ["f1", "f2"]

    def test_modello_nudo_rifiutato(self, tmp_path):
        _artifact, path = _make_artifact(tmp_path, as_dict=False)
        with pytest.raises(ValueError, match="non standardizzato"):
            load_artifact(path)

    def test_chiavi_mancanti_rifiutate(self, tmp_path):
        artifact = {
            "model_type": "random_forest",
            "model": _FakeTabularModel(),
            # mancano feature_names e threshold
        }
        path = tmp_path / "incompleto.pkl"
        joblib.dump(artifact, path)
        with pytest.raises(ValueError, match="Chiavi mancanti"):
            load_artifact(path)


class TestPredictProbaBundle:
    def test_modello_tabulare_con_scaler(self, tmp_path):
        from sklearn.preprocessing import StandardScaler

        df = pd.DataFrame({"f1": [1.0, 2.0, 3.0], "f2": [10.0, 20.0, 30.0]})
        scaler = StandardScaler().fit(df.values)
        artifact, _ = _make_artifact(
            tmp_path, scaler=scaler, threshold=0.5,
            feature_names=("f1", "f2"),
        )
        out_df, proba = predict_proba_bundle(artifact, df)
        assert len(out_df) == 3
        assert len(proba) == 3
        # Il preprocessing e' identico al training: ffill+fillna(0)+scaler
        X = scaler.transform(df[["f1", "f2"]].values)
        assert np.allclose(proba, np.clip(X.mean(axis=1), 0, 1))

    def test_feature_mancanti_rifiutate(self, tmp_path):
        artifact, _ = _make_artifact(tmp_path, feature_names=("f1", "f2"))
        df = pd.DataFrame({"f1": [1.0]})  # manca f2
        with pytest.raises(KeyError, match="f2"):
            predict_proba_bundle(artifact, df)

    def test_imputazione_causale_nan(self, tmp_path):
        """NaN riempiti con ffill+0 come nel training: nessun NaN al modello."""
        artifact, _ = _make_artifact(tmp_path, feature_names=("f1", "f2"))
        df = pd.DataFrame({"f1": [np.nan, 2.0], "f2": [1.0, np.nan]})
        _out, proba = predict_proba_bundle(artifact, df)
        assert not np.isnan(proba).any()

    def test_transformer_richiede_lookback(self, tmp_path):
        """Senza torch installato il ramo transformer salta: qui si verifica
        solo la soglia minima di finestre, usando un fake con eval/tensor."""
        df = pd.DataFrame({"f1": range(5), "f2": range(5)}, dtype=float)
        artifact = {
            "model_type": "transformer",
            "model": object(),  # mai raggiunto: len(X) < lookback
            "scaler": None,
            "feature_names": ["f1", "f2"],
            "threshold": 0.5,
            "lookback": 10,
        }
        with pytest.raises(ValueError, match="almeno 10 ore"):
            predict_proba_bundle(artifact, df)
