#!/usr/bin/env python3
"""
Ottimizzazione bayesiana (Optuna/TPE) degli iperparametri del Transformer.

Cosa ottimizza: lookback (finestra temporale), learning rate, dropout,
d_model e weight_decay — tutti gli iperparametri introdotti con la
regolarizzazione esplicita e prima mai tarati (lookback=10 era un default
euristico, non ottimizzato).

Cosa NON fa: NON tocca MAI il test set durante la ricerca. L'obiettivo
(objective) e' il MCC sul validation (la coda del 15% del train, la stessa
usata dal checkpoint di train_temporal_transformer). Il test va valutato UNA
sola volta, alla fine, con train_risk_model.py e la configurazione vincitrice
stampata da questo script — ottimizzare sul test sarebbe data leakage puro.

Esempio d'uso:
    python -m scripts.tune_transformer --trials 40 --epochs 20
    python -m scripts.tune_transformer --trials 20 --seed 42 --output-csv runs/tuning/optuna_trials.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import numpy as np
import pandas as pd

from scripts.utils import setup_logger

logger = setup_logger("tune_transformer")

# Spazio di ricerca: scelto sui regimi fisici dei Campi Flegrei.
LOOKBACK_CHOICES = (6, 8, 12, 24, 48)
D_MODEL_CHOICES = (16, 32, 64)
LR_RANGE = (1e-5, 1e-3)          # log-uniform
DROPOUT_RANGE = (0.1, 0.5)       # uniform
WEIGHT_DECAY_RANGE = (1e-4, 1e-1) # log-uniform


def suggest_params(trial) -> dict:
    """Estrae un set di iperparametri dal trial Optuna (TPE)."""
    return {
        "sequence_length": trial.suggest_categorical(
            "sequence_length", list(LOOKBACK_CHOICES)
        ),
        "learning_rate": trial.suggest_float(
            "learning_rate", *LR_RANGE, log=True
        ),
        "dropout": trial.suggest_float("dropout", *DROPOUT_RANGE),
        "d_model": trial.suggest_categorical("d_model", list(D_MODEL_CHOICES)),
        "weight_decay": trial.suggest_float(
            "weight_decay", *WEIGHT_DECAY_RANGE, log=True
        ),
    }


def make_objective(X_train, y_train, epochs: int, seed: int | None):
    """Costruisce la funzione obiettivo Optuna: MCC sul validation.

    Il validation e' interno a train_temporal_transformer (coda del 15% del
    train, checkpoint su val MCC): nessun contatto col test set.
    """
    import torch

    def objective(trial) -> float:
        params = suggest_params(trial)

        # Riproducibilita' del singolo trial (il TPE resta sequenziale-deterministico)
        if seed is not None:
            torch.manual_seed(seed)
            np.random.seed(seed)

        # Import locale: evita costi di import se optuna non serve
        from scripts.train_risk_model import train_temporal_transformer

        try:
            _model, res = train_temporal_transformer(
                X_train,
                y_train,
                X_test=None,  # mai il test nella ricerca
                y_test=None,
                epochs=epochs,
                learning_rate=params["learning_rate"],
                sequence_length=params["sequence_length"],
                dropout=params["dropout"],
                d_model=params["d_model"],
                weight_decay=params["weight_decay"],
            )
        except Exception as exc:  # noqa: BLE001
            # Trial fallito (es. lookback > finestre disponibili): lo scarta,
            # non ferma lo studio
            logger.warning(f"Trial {trial.number} fallito: {exc}")
            return float("-inf")

        # MCC val del checkpoint: la stessa metrica usata in produzione
        val_mcc = res.get("params", {}).get("validation_mcc", 0.0) or 0.0
        val_mcc = float(val_mcc) if not np.isnan(val_mcc) else 0.0
        logger.info(
            f"Trial {trial.number}: lookback={params['sequence_length']} "
            f"lr={params['learning_rate']:.2e} dropout={params['dropout']:.2f} "
            f"d_model={params['d_model']} wd={params['weight_decay']:.1e} "
            f"-> MCC val {val_mcc:.4f}"
        )
        return val_mcc

    return objective


def format_train_command(best: dict) -> str:
    """Comando train_risk_model pronto con la configurazione vincitrice."""
    return (
        "python scripts/train_risk_model.py \\\n"
        f"  --sequence-length {best['sequence_length']} \\\n"
        f"  --learning-rate {best['learning_rate']:.6g}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ottimizzazione Optuna degli iperparametri del Transformer"
    )
    parser.add_argument(
        "--trials", type=int, default=40,
        help="Numero di trial TPE (default: 40; ~20s ciascuno su 2.000 ore)",
    )
    parser.add_argument(
        "--epochs", type=int, default=20,
        help="Budget di epoche per trial (early stopping interno al solito)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Seed per la riproducibilita' dei singoli trial",
    )
    parser.add_argument(
        "--output-csv", type=Path, default=None,
        help="CSV con tutti i trial (default: runs/tuning/optuna_trials.csv)",
    )
    args = parser.parse_args()

    import optuna

    # I dati arrivano dallo stesso posto del training: vista ML nel DuckDB
    from scripts.train_risk_model import (
        load_data_from_db,
        prepare_features,
        split_data_temporal,
    )

    logger.info("Caricamento dataset dalla vista ML (split temporale 80/20)...")
    df = load_data_from_db()
    df_train, _df_test = split_data_temporal(df, test_size=0.2)
    X_train, y_train = prepare_features(df_train)
    logger.info(
        f"Train per la ricerca: {len(X_train)} righe, "
        f"{(y_train == 1).sum()} positive. Il TEST NON verra' toccato."
    )

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=args.seed),
    )
    objective = make_objective(X_train, y_train, epochs=args.epochs, seed=args.seed)
    study.optimize(objective, n_trials=args.trials, show_progress_bar=False)

    best = study.best_params
    logger.info("=" * 60)
    logger.info("MIGLIOR CONFIGURAZIONE (MCC val: %.4f)", study.best_value)
    for k, v in best.items():
        logger.info(f"  {k}: {v}")
    logger.info("Comando di training pronto (valutare il test UNA sola volta):")
    logger.info(format_train_command(best))
    logger.info("=" * 60)

    # Report dei trial
    out_csv = args.output_csv or (PROJECT_ROOT / "runs" / "tuning" / "optuna_trials.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df_trials = study.trials_dataframe()
    df_trials.to_csv(out_csv, index=False)
    logger.info(f"Trial salvati in: {out_csv}")


if __name__ == "__main__":
    main()
