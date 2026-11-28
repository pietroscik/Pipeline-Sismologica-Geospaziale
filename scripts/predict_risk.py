#!/usr/bin/env python3
"""
Inferenza standardizzata per il modello di rischio sismico.

Carica il bundle .pkl prodotto da train_risk_model.py (model_type, model,
scaler, feature_names, threshold, lookback) e applica ESATTAMENTE lo stesso
preprocessing del training, azzerando il train-serve skew. Gestisce sia il
Temporal Transformer (finestre 3D con lookback) sia i modelli tabulari
(XGBoost / Random Forest).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_artifact(model_path: Path) -> dict:
    """Carica e valida il bundle .pkl standardizzato."""
    artifact = joblib.load(model_path)
    if not isinstance(artifact, dict) or "model" not in artifact:
        raise ValueError(
            f"Artefatto {model_path} non standardizzato (manca il dict bundle). "
            "Rigenera il .pkl con il packaging di train_risk_model.py."
        )
    required = {"model_type", "model", "feature_names", "threshold"}
    missing = required - set(artifact.keys())
    if missing:
        raise ValueError(f"Chiavi mancanti nell'artefatto .pkl: {missing}")
    return artifact


def load_features_from_db(
    db_path: Path, view_name: str, last_n_hours: int | None
) -> pd.DataFrame:
    """Legge la vista ML dal DuckDB, ordinata temporalmente."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        query = f'SELECT * FROM {view_name} ORDER BY "timestamp"'
        df = con.execute(query).df()
    if last_n_hours is not None and last_n_hours > 0:
        df = df.iloc[-last_n_hours:].copy()
    return df.reset_index(drop=True)


def predict_proba_bundle(
    artifact: dict, df: pd.DataFrame
) -> tuple[pd.DataFrame, np.ndarray]:
    """Applica il preprocessing del training e predice le probabilita'."""
    feature_names = artifact["feature_names"]
    missing_cols = [c for c in feature_names if c not in df.columns]
    if missing_cols:
        raise KeyError(f"Feature attese dal modello ma assenti nella vista: {missing_cols}")

    # 1. Imputazione causale identica al training
    X = df[feature_names].ffill().fillna(0).values

    # 2. Scaling (se presente nell'artefatto)
    scaler = artifact.get("scaler")
    if scaler is not None:
        X = scaler.transform(X)

    model_type = str(artifact["model_type"]).lower()
    model = artifact["model"]

    # 3. Inferenza per architettura
    if model_type == "transformer":
        lookback = int(artifact.get("lookback") or 10)
        # Fail-fast PRIMA dell'import di torch: la soglia minima di finestre
        # e' un errore di input, non dipende dalla libreria
        if len(X) < lookback:
            raise ValueError(
                f"Servono almeno {lookback} ore per il Transformer, trovate {len(X)}."
            )
        import torch

        # Finestre 3D allineate a create_temporal_sequences: la riga i usa le
        # ore [i-lookback+1 .. i] (la finestra termina sull'ora corrente).
        X_3d = np.array([X[i - lookback + 1 : i + 1] for i in range(lookback - 1, len(X))])
        model.eval()
        with torch.no_grad():
            logits = model(torch.tensor(X_3d, dtype=torch.float32)).squeeze(-1)
            proba = torch.sigmoid(logits).cpu().numpy().reshape(-1)
        out_df = df.iloc[lookback - 1 :].copy().reset_index(drop=True)

    elif model_type == "xgboost":
        import xgboost as xgb

        if isinstance(model, xgb.Booster):
            dmat = xgb.DMatrix(X, feature_names=feature_names)
            proba = model.predict(dmat)
        else:
            proba = model.predict_proba(X)[:, 1]
        out_df = df.copy()

    else:
        # Random Forest o classificatori sklearn standard
        proba = model.predict_proba(X)[:, 1]
        out_df = df.copy()

    return out_df, np.asarray(proba, dtype=float)


def main():
    parser = argparse.ArgumentParser(
        description="Inferenza rischio sismico da bundle .pkl"
    )
    parser.add_argument(
        "--model-path", type=Path, required=True, help="Percorso al file .pkl bundle"
    )
    parser.add_argument(
        "--db-path", type=Path, default=PROJECT_ROOT / "data" / "db" / "seismic_output.duckdb"
    )
    parser.add_argument("--view-name", type=str, default="ml_features_ready_view")
    parser.add_argument(
        "--last-n-hours", type=int, default=417,
        help="Ultime N ore da valutare (default: 417, size del test reale)",
    )
    parser.add_argument(
        "--output-csv", type=Path, default=None,
        help="CSV opzionale con le predizioni",
    )
    parser.add_argument(
        "--evaluate", action="store_true",
        help="Calcola la matrice di confusione su Target_Allarme",
    )
    args = parser.parse_args()

    artifact = load_artifact(args.model_path)
    df = load_features_from_db(args.db_path, args.view_name, args.last_n_hours)
    out_df, proba = predict_proba_bundle(artifact, df)

    thr = float(artifact["threshold"])
    preds = (proba >= thr).astype(int)

    out_df["risk_proba"] = proba
    out_df["risk_alert"] = preds

    latest = out_df.iloc[-1]
    print(
        f"Modello: {artifact['model_type']} | Soglia: {thr:.4f} | "
        f"Finestre valutate: {len(out_df)}"
    )
    print(
        f"Range probabilit\u00e0: min={proba.min():.4f}, "
        f"median={np.median(proba):.4f}, max={proba.max():.4f}"
    )
    print(
        f"Ultimo stato ({latest['timestamp']}): "
        f"prob={latest['risk_proba']:.4f} -> "
        f"{'ALLARME' if latest['risk_alert'] == 1 else 'NORMALE'}"
    )

    if args.evaluate and "Target_Allarme" in out_df.columns:
        from sklearn.metrics import (
            confusion_matrix,
            f1_score,
            matthews_corrcoef,
            precision_score,
            recall_score,
        )

        y_true = out_df["Target_Allarme"].astype(int).values
        tn, fp, fn, tp = confusion_matrix(y_true, preds).ravel()
        print("\n--- VALUTAZIONE SU TARGET_ALLARME ---")
        print(f"TP: {tp:3d} | FP: {fp:3d}")
        print(f"FN: {fn:3d} | TN: {tn:3d}")
        print(
            f"Precision: {precision_score(y_true, preds, zero_division=0):.4f} | "
            f"Recall: {recall_score(y_true, preds, zero_division=0):.4f} | "
            f"F1: {f1_score(y_true, preds, zero_division=0):.4f} | "
            f"MCC: {matthews_corrcoef(y_true, preds):.4f}"
        )

    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        cols_to_save = ["timestamp", "risk_proba", "risk_alert"]
        if "Target_Allarme" in out_df.columns:
            cols_to_save.append("Target_Allarme")
        out_df[cols_to_save].to_csv(args.output_csv, index=False)
        print(f"Predizioni salvate in: {args.output_csv}")


if __name__ == "__main__":
    main()
