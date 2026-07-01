#!/usr/bin/env python3
"""
Script di Inferenza in Tempo Reale.
Carica un modello addestrato (.pkl o .pth) e valuta i nuovi dati sismici.

Usage:
    python scripts/predict_live.py \
        --model mobile/models/best_xgboost_modello_rischio_20260604.pkl \
        --data examples/mobile_devices/dataset_ml_sismico.csv
"""

import argparse
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
import sys

from utils import setup_logger, get_project_root

logger = setup_logger("predict_live")
PROJECT_ROOT = get_project_root()

# Importiamo le logiche di feature engineering dallo script di training
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "mobile_devices"))
from train_modello import prepare_features, create_temporal_sequences
sys.path.insert(0, str(PROJECT_ROOT / "mobile"))
from alert_system import get_alert_system

def main():
    parser = argparse.ArgumentParser(description="Inferenza su nuovi dati sismici")
    parser.add_argument("--model", type=Path, required=True, help="Path al modello (.pkl o .pth)")
    parser.add_argument("--data", type=Path, required=True, help="Path al file CSV dei nuovi dati")
    parser.add_argument("--sequence-length", type=int, default=10, help="Lunghezza finestra per Transformer")
    parser.add_argument("--threshold", type=float, default=0.7, help="Soglia di allarme (0-1)")
    args = parser.parse_args()

    if not args.model.exists():
        logger.error(f"❌ Modello non trovato: {args.model}")
        sys.exit(1)
        
    if not args.data.exists():
        logger.error(f"❌ Dati non trovati: {args.data}")
        sys.exit(1)

    logger.info(f"📊 Caricamento nuovi dati da: {args.data.name}")
    df = pd.read_csv(args.data)
    
    # Gestione dell'indice temporale come avviene durante l'addestramento
    if 'Tempo' in df.columns:
        df['Tempo'] = pd.to_datetime(df['Tempo'])
        df.set_index('Tempo', inplace=True)
    elif df.index.name is None or df.index.name == 'Unnamed: 0':
        datetime_cols = [col for col in df.columns if 'time' in col.lower() or 'tempo' in col.lower()]
        if datetime_cols:
            df[datetime_cols[0]] = pd.to_datetime(df[datetime_cols[0]])
            df.set_index(datetime_cols[0], inplace=True)
            
    # Usiamo prepare_features, e gestiamo l'eventualità che il Target non ci sia (essendo dati futuri)
    if 'Target_Allarme' not in df.columns:
        df['Target_Allarme'] = 0  # Dummy colonna per non far arrabbiare la funzione
        
    X, y_dummy = prepare_features(df, target_column='Target_Allarme')
    
    prob_rischio = 0.0

    if args.model.suffix == ".pkl":
        logger.info("🌲 Caricamento modello Machine Learning (XGBoost / RF)...")
        model = joblib.load(args.model)
        
        # Prediciamo solo l'ultima riga (il momento attuale)
        X_latest = X.iloc[[-1]]
        
        if "xgboost" in str(type(model)).lower():
            import xgboost as xgb
            dmatrix = xgb.DMatrix(X_latest)
            prob_rischio = float(model.predict(dmatrix)[0])
        else:
            prob_rischio = float(model.predict_proba(X_latest)[0, 1])

    elif args.model.suffix == ".pth":
        logger.info("🤖 Caricamento modello Deep Learning (Transformer)...")
        import torch
        from train_modello import SeismicTransformer
        
        model = SeismicTransformer(num_features=X.shape[1])
        model.load_state_dict(torch.load(args.model))
        model.eval()
        
        # Creiamo la sequenza temporale per la rete neurale
        X_3d, _ = create_temporal_sequences(X, y_dummy, args.sequence_length)
        X_latest_3d = torch.tensor(X_3d[-1:]).float()
        
        with torch.no_grad():
            logits = model(X_latest_3d)
            prob_rischio = float(torch.sigmoid(logits).squeeze().numpy())
            
    else:
        logger.error("Estensione modello non supportata (.pkl o .pth)")
        sys.exit(1)

    # ---------------------------------------------------------
    # EMISSIONE DEL RISULTATO
    # ---------------------------------------------------------
    logger.info("=" * 50)
    logger.info(f"🔎 LIVELLO DI RISCHIO CALCOLATO: {prob_rischio:.2%}")
    if prob_rischio >= args.threshold:
        logger.critical(f"⚠️ ATTENZIONE! SOGLIA SUPERATA! E' CONSIGLIATO ATTIVARE L'ALLARME.")
        
        # Innesca il sistema di allarme reale
        try:
            alert_sys = get_alert_system()
            if not alert_sys.active_alert:
                latest_time = str(df.index[-1]) if isinstance(df.index, pd.DatetimeIndex) else "Ora Attuale"
                alert_sys.trigger_alert(
                    risk_level=prob_rischio,
                    triggering_stations=int(X_latest.get('numero_eventi', 18)),
                    timestamp=latest_time
                )
                logger.info("✅ Allarme inviato con successo tramite AlertSystem!")
            else:
                logger.info("ℹ️ Un allarme è già attivo. Nessuna nuova notifica inviata per evitare spam.")
        except Exception as e:
            logger.error(f"❌ Errore durante l'invio dell'allarme: {e}")

if __name__ == "__main__":
    main()