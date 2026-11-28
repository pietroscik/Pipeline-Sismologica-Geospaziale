"""
Test per scripts/tune_transformer.py: ottimizzazione Optuna/TPE degli
iperparametri del Transformer (lookback, lr, dropout, d_model, weight_decay).

Il principio chiave: la ricerca NON tocca mai il test set — l'objective e'
il MCC sul validation interno a train_temporal_transformer.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

optuna = pytest.importorskip("optuna")

from scripts.tune_transformer import (  # noqa: E402
    D_MODEL_CHOICES,
    DROPOUT_RANGE,
    LR_RANGE,
    LOOKBACK_CHOICES,
    WEIGHT_DECAY_RANGE,
    format_train_command,
    make_objective,
    suggest_params,
)


class _FakeTrial(optuna.trial.Trial):
    """Trial su uno studio monouso: campiona dai range dichiarati."""


def _new_trial():
    study = optuna.create_study(direction="maximize")
    return study.ask()


class TestSuggestParams:
    def test_range_dichiarati(self):
        trial = _new_trial()
        p = suggest_params(trial)
        assert p["sequence_length"] in LOOKBACK_CHOICES
        assert p["d_model"] in D_MODEL_CHOICES
        assert LR_RANGE[0] <= p["learning_rate"] <= LR_RANGE[1]
        assert DROPOUT_RANGE[0] <= p["dropout"] <= DROPOUT_RANGE[1]
        assert WEIGHT_DECAY_RANGE[0] <= p["weight_decay"] <= WEIGHT_DECAY_RANGE[1]

    def test_lookback_ottimizzabile_non_hardcodato(self):
        """Il default euristico 10 non deve essere vincolante: la griglia
        deve contenere scale diverse (6 ore -> 48 ore)."""
        assert 10 not in LOOKBACK_CHOICES  # griglia cosciente, non default pigro
        assert LOOKBACK_CHOICES[0] < 10 < LOOKBACK_CHOICES[-1]


class TestObjective:
    def test_mai_il_test_set(self, monkeypatch):
        """L'objective chiamava train_temporal_transformer con X_test=None:
        il test set non entra mai nella ricerca (anti data leakage)."""
        calls = {}

        def fake_train(X_train, y_train, X_test=None, y_test=None, **kw):
            calls["X_test"] = X_test
            calls["y_test"] = y_test
            return object(), {"params": {"validation_mcc": 0.42}}

        monkeypatch.setattr(
            "scripts.train_risk_model.train_temporal_transformer", fake_train
        )
        rng = np.random.default_rng(0)
        n = 60
        X = pd.DataFrame({"f1": rng.normal(size=n)})
        y = pd.Series((rng.random(n) < 0.2).astype(int))
        objective = make_objective(X, y, epochs=1, seed=0)
        trial = _new_trial()
        value = objective(trial)
        assert value == pytest.approx(0.42)
        assert calls["X_test"] is None and calls["y_test"] is None

    def test_trial_fallito_non_ferma_lo_studio(self, monkeypatch):
        def fake_train(*a, **kw):
            raise RuntimeError("lookback troppo grande")

        monkeypatch.setattr(
            "scripts.train_risk_model.train_temporal_transformer", fake_train
        )
        rng = np.random.default_rng(0)
        X = pd.DataFrame({"f1": rng.normal(size=50)})
        y = pd.Series(np.zeros(50, dtype=int))
        objective = make_objective(X, y, epochs=1, seed=0)
        trial = _new_trial()
        assert objective(trial) == float("-inf")

    def test_mcc_nan_mappato_a_zero(self, monkeypatch):
        def fake_train(*a, **kw):
            return object(), {"params": {"validation_mcc": float("nan")}}

        monkeypatch.setattr(
            "scripts.train_risk_model.train_temporal_transformer", fake_train
        )
        rng = np.random.default_rng(0)
        X = pd.DataFrame({"f1": rng.normal(size=50)})
        y = pd.Series(np.zeros(50, dtype=int))
        objective = make_objective(X, y, epochs=1, seed=0)
        trial = _new_trial()
        assert objective(trial) == 0.0


class TestFormatTrainCommand:
    def test_comando_pronto(self):
        cmd = format_train_command(
            {"sequence_length": 24, "learning_rate": 0.000123}
        )
        assert "--sequence-length 24" in cmd
        assert "--learning-rate 0.000123" in cmd
        assert "train_risk_model" in cmd
