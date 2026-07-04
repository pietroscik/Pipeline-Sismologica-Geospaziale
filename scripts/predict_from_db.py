#!/usr/bin/env python3
"""
Script per eseguire inferenza utilizzando un modello addestrato e i dati
più recenti disponibili nel database DuckDB.
"""

import argparse
import duckdb
import joblib
import numpy as np
from pathlib import Path
import pandas as pd
from feature_engineering import calculate_rolling_b_value
from scripts.utils import setup_logger

logger = setup_logger("inference")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH = PROJECT_ROOT / "data" / "db" / "seismic_output.duckdb"
MODELS_DIR = PROJECT_ROOT / "models"


def load_data_from_db(limit: int = None) -> pd.DataFrame:
    """Carica i dati dalla vista ml_features_ready_view per l'inferenza."""
    logger.info(f"📖 Caricamento dati per inferenza da: {DUCKDB_PATH}")
    if not DUCKDB_PATH.exists():
        logger.error(f"Database non trovato in {DUCKDB_PATH}. Eseguire prima la pipeline con --auto-ingest.")
        return pd.DataFrame()

    con = duckdb.connect(database=str(DUCKDB_PATH), read_only=True)
    query = "SELECT * FROM ml_features_ready_view ORDER BY timestamp DESC"
    if limit:
        query += f" LIMIT {limit}"
    df = con.execute(query).fetch_df()
    con.close()
    logger.info(f"Caricati {len(df)} record.")
    return df


def find_latest_model(model_dir: Path) -> Path | None:
    """Trova il file del modello più recente (.pkl o .pth) in una directory."""
    candidates = list(model_dir.glob("*.pkl")) + list(model_dir.glob("*.pth"))
    if not candidates:
        return None
    latest_model = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest_model


def main():
    parser = argparse.ArgumentParser(description="Esegue inferenza con un modello addestrato.")
    parser.add_argument("--model-dir", type=Path, default=MODELS_DIR,
                        help=f"Directory contenente i modelli (default: {MODELS_DIR})")
    parser.add_argument("--limit", type=int, default=100,
                        help="Limita il numero di record da processare dal DB (default: 100 più recenti)")
    args = parser.parse_args()

    # Carica dati
    df = load_data_from_db(args.limit)
    if df.empty:
        return

    # --- Feature Engineering (deve essere identico al training) ---
    logger.info("🔬 Calcolo feature aggiuntive (b-value)...")
    if 'numero_eventi' in df.columns:
        # Ordina per timestamp per un calcolo corretto del rolling
        df = df.sort_values('timestamp').reset_index(drop=True)
        df['bvalue_rolling_24h'] = calculate_rolling_b_value(df['numero_eventi'], window_size=24)
        logger.info("✅ Feature b-value calcolata.")

    # Carica modello
    latest_model_path = find_latest_model(args.model_dir)
    if not latest_model_path:
        logger.error(f"Nessun modello (.pkl o .pth) trovato in: {args.model_dir}")
        return

    # Gestione preliminare per tipi di modello diversi
    if latest_model_path.suffix == '.pth':
        logger.error(f"Caricamento modelli PyTorch (.pth) non è supportato da questo script.")
        logger.error("È necessario un flusso di inferenza dedicato che prepari i dati in sequenze 3D.")
        return

    logger.info(f"Caricamento del modello più recente: {latest_model_path.name}")
    model = joblib.load(latest_model_path)

    # --- Logica di Predizione Robusta ---
    # Assicura che le colonne del DataFrame corrispondano a quelle usate per il training.
    # Questa logica gestisce i diversi tipi di modelli salvati come .pkl.
    try:
        if hasattr(model, 'get_booster'):  # Modello XGBoost
            logger.info("Rilevato modello XGBoost.")
            model_features = model.get_booster().feature_names
            X = df[model_features]
            # XGBoost nativo predice score, non probabilità con predict_proba
            import xgboost as xgb
            dmatrix = xgb.DMatrix(X)
            predictions = model.predict(dmatrix)
        elif hasattr(model, 'feature_names_in_'):  # Modello Scikit-learn (es. RandomForest)
            logger.info("Rilevato modello scikit-learn.")
            model_features = model.feature_names_in_
            X = df[model_features]
            predictions = model.predict_proba(X)[:, 1]
        else:
            raise TypeError("Tipo di modello .pkl non riconosciuto (né XGBoost né scikit-learn).")

    except Exception as e:
        logger.error(f"❌ Errore durante la preparazione per la predizione: {e}")
        logger.error("   Verificare che le feature nel DB ('ml_features_ready_view') corrispondano a quelle usate per il training del modello.")
        return

    df["risk_score"] = predictions

    # Salva risultati
    output_dir = PROJECT_ROOT / "runs" / "inference_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"predictions_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(output_path, index=False)
    logger.info(f"✅ Risultati dell'inferenza salvati in: {output_path}")

if __name__ == "__main__":
    main()