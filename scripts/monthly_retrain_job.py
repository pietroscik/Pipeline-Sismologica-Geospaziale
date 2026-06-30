#!/usr/bin/env python3
"""
Script per il riaddestramento mensile automatizzato del modello ML.
1. Esegue il training e il confronto dei modelli (train_modello.py).
2. Salva il modello vincitore nella cartella `mobile/models/` in modo che 
   il `nightly_job.py` inizi ad usarlo automaticamente dal giorno successivo.
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime

from utils import setup_logger, get_project_root

logger = setup_logger("monthly_retrain_job")
PROJECT_ROOT = get_project_root()

def run_command(cmd: list, error_msg: str) -> bool:
    logger.info("Esecuzione comando di addestramento...")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"{error_msg}. Exit code: {e.returncode}")
        return False

def main():
    python_exe = sys.executable
    
    logger.info("=" * 60)
    logger.info(f"🔄 AVVIO JOB DI RIADDESTRAMENTO MENSILE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    dataset_csv = PROJECT_ROOT / "examples" / "mobile_devices" / "dataset_ml_sismico.csv"
    output_dir = PROJECT_ROOT / "mobile" / "models"
    
    if not dataset_csv.exists():
        logger.error(f"Dataset ML non trovato: {dataset_csv}")
        sys.exit(1)

    logger.info("Fase 1: Addestramento Modelli in competizione (XGBoost, RF, Transformer)...")
    
    train_cmd = [
        python_exe, str(PROJECT_ROOT / "examples" / "mobile_devices" / "train_modello.py"),
        "--dataset", str(dataset_csv),
        "--model-output-dir", str(output_dir),
        "--model-type", "compare",
        "--epochs", "50",
        "--learning-rate", "0.001",
        "--sequence-length", "10"
    ]
    
    if not run_command(train_cmd, "Errore durante l'addestramento dei modelli"):
        logger.error("Job mensile interrotto.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("✅ JOB MENSILE COMPLETATO!")
    logger.info("Il nuovo modello vincitore è stato salvato.")
    logger.info("Il nightly_job lo adotterà automaticamente per le predizioni future.")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()