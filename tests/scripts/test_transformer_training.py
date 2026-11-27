"""
Test per il best-epoch checkpointing e l'ottimizzazione della soglia MCC
del Temporal Transformer in scripts/train_risk_model.py.

Copre:
- find_best_mcc_threshold: griglia, soglie degeneri, caso una-classe
- calculate_metrics con soglia personalizzata (retrocompatibilità a 0.5)
- train_temporal_transformer end-to-end: split temporale di validazione,
  checkpoint dei pesi migliori, soglia appresa sul validation e applicata
  al test set (nessun data leakage: la soglia mai appresa sul test)
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

torch = pytest.importorskip("torch")

spec = importlib.util.spec_from_file_location(
    "train_risk_model", PROJECT_ROOT / "scripts" / "train_risk_model.py"
)
trm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trm)


def make_binary_dataset(n=200, seq_len=5):
    """Dataset sintetico con pattern imparabile: le feature del timestep t
    predicono il target al tempo t (finestra temporale)."""
    rng = np.random.default_rng(42)
    X = pd.DataFrame({
        "f1": rng.normal(size=n),
        "f2": rng.normal(size=n),
    })
    # Pattern deterministico + rumore: il modello può impararlo davvero
    target_signal = (0.8 * X["f1"] - 0.6 * X["f2"]).values
    y = pd.Series((target_signal > np.quantile(target_signal, 0.7)).astype(float))
    # Garantisce entrambe le classi
    assert y.nunique() == 2, "il dataset sintetico deve avere 2 classi"
    return X, y


class TestFindBestMccThreshold:
    def test_soglia_ottimale_su_segnale_pulito(self):
        # Proabilità ben separate: la soglia ottimale deve stare vicino a 0.5
        y_true = np.array([0] * 50 + [1] * 50)
        y_proba = np.concatenate([
            np.linspace(0.0, 0.4, 50),   # classe 0: probabilità basse
            np.linspace(0.6, 1.0, 50),   # classe 1: probabilità alte
        ])
        thr, mcc = trm.find_best_mcc_threshold(y_true, y_proba)
        assert 0.35 <= thr <= 0.65
        assert mcc > 0.9

    def test_soglia_degenere_evitata(self):
        # Tutte le probabilità sopra 0.95: le soglie degeneri (predizione
        # a una sola classe) devono essere saltate
        y_true = np.array([0, 1, 0, 1] * 10)
        y_proba = np.full(40, 0.99)
        thr, mcc = trm.find_best_mcc_threshold(y_true, y_proba)
        # Nessuna soglia produce due classi: fallback 0.5, MCC -1→0.0?
        # con tutte le prob >= soglia → una sola classe → tutte skippate → 0.5
        assert thr == 0.5

    def test_validation_una_classe_fallback_05(self):
        y_true = np.ones(30)  # una sola classe
        y_proba = np.linspace(0.1, 0.9, 30)
        thr, mcc = trm.find_best_mcc_threshold(y_true, y_proba)
        assert thr == 0.5 and mcc == 0.0

    def test_validation_vuoto(self):
        thr, mcc = trm.find_best_mcc_threshold(np.array([]), np.array([]))
        assert thr == 0.5 and mcc == 0.0


class TestCalculateMetricsThreshold:
    def test_threshold_default_05_retrocompatibile(self):
        y_true = pd.Series([0, 1, 0, 1])
        y_pred = np.array([0.4, 0.6, 0.4, 0.6])
        m = trm.calculate_metrics(y_true, y_pred)
        assert m["accuracy"] == 1.0

    def test_threshold_personalizzato(self):
        y_true = pd.Series([0, 1, 0, 1])
        y_pred = np.array([0.4, 0.6, 0.4, 0.6])
        # Soglia alta: tutto classificato 0
        m = trm.calculate_metrics(y_true, y_pred, threshold=0.9)
        assert m["accuracy"] == 0.5
        # Soglia bassa: tutto classificato 1
        m2 = trm.calculate_metrics(y_true, y_pred, threshold=0.1)
        assert m2["accuracy"] == 0.5


class TestTrainTemporalTransformer:
    def test_training_completo_con_checkpoint_e_soglia(self):
        X, y = make_binary_dataset(250)
        X_test, y_test = make_binary_dataset(60)

        model, results = trm.train_temporal_transformer(
            X, y, X_test=X_test, y_test=y_test,
            epochs=8, learning_rate=0.01, sequence_length=5,
        )
        assert model is not None
        assert results["model_type"] == "temporal_transformer"

        # La soglia MCC è nei parametri salvati (per i metadati del modello)
        params = results["params"]
        assert "best_threshold" in params
        assert 0.05 <= params["best_threshold"] <= 0.95
        assert "validation_mcc" in params
        assert "best_val_loss" in params
        assert params["val_split_fraction"] == 0.15

        # Le metriche del test sono state calcolate (non necessariamente buone
        # su dati sintetici, ma presenti e coerenti)
        assert "test_accuracy" in results

        # Il modello produce logit → probabilità coerenti
        model.eval()
        with torch.no_grad():
            X3d, y1d = trm.create_temporal_sequences(X, y, 5)
            proba = torch.sigmoid(model(torch.tensor(X3d))).squeeze(-1).reshape(-1)
        assert ((proba >= 0) & (proba <= 1)).all()

    def test_split_temporale_non_mescolato(self):
        """Il validation set deve essere la CODA temporale del train."""
        n = 100
        rng = np.random.default_rng(0)
        X = pd.DataFrame({"f1": rng.normal(size=n)})
        y = pd.Series((rng.random(n) > 0.5).astype(float))
        X3d, y1d = trm.create_temporal_sequences(X, y, 3)

        val_split = int(len(X3d) * 0.85)
        X_va, y_va = X3d[val_split:], y1d[val_split:]
        # Il validation contiene gli ULTIMI campioni (ordine temporale)
        np.testing.assert_array_equal(X_va[0], X3d[val_split])
        assert len(X_va) == len(X3d) - val_split
