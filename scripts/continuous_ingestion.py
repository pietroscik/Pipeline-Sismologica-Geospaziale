#!/usr/bin/env python3
"""
Script per l'ingestione continua e l'elaborazione dei dati sismici.
Implementa la strategia di Micro-Batching: Scarica -> Estrai Metriche -> Salva -> Elimina Seed.

Usage:
    python scripts/continuous_ingestion.py \
        --start-date 2024-01-01 \
        --end-date 2024-01-31 \
        --window-days 1 \
        --output-master runs/master_dataset/master_deltas.csv
"""

import argparse
import subprocess
import shutil
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

from utils import setup_logger, get_project_root

logger = setup_logger("continuous_ingestion", level="INFO")
PROJECT_ROOT = get_project_root()

def run_command(cmd: list, error_msg: str) -> bool:
    """Esegue un comando subprocess e gestisce gli errori."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"{error_msg}. Exit code: {e.returncode}")
        logger.error(f"Dettagli: {e.stderr}")
        return False

def append_to_master(daily_csv: Path, master_csv: Path):
    """Aggiunge i risultati giornalieri al dataset master."""
    if not daily_csv.exists() or daily_csv.stat().st_size == 0:
        logger.warning(f"Nessun dato da appendere in {daily_csv.name}")
        return

    df_daily = pd.read_csv(daily_csv)
    if df_daily.empty:
        return

    if not master_csv.exists():
        # Crea il master file con gli header
        df_daily.to_csv(master_csv, index=False)
        logger.info(f"Creato master dataset in {master_csv}")
    else:
        # Appende senza sovrascrivere
        df_daily.to_csv(master_csv, mode='a', header=False, index=False)
        logger.info(f"Aggiunti {len(df_daily)} record al master dataset.")


def update_ml_features(master_csv: Path, ml_csv: Path):
    """Traduce i delta crudi (FDSN) in feature temporali orarie per il Modello ML."""
    if not master_csv.exists() or master_csv.stat().st_size == 0:
        return
        
    logger.info("Fase 5: Traduzione dati crudi in feature per il Machine Learning...")
    df = pd.read_csv(master_csv)
    
    if 'arrival_iso' not in df.columns:
        logger.warning("Dati insufficienti o colonna 'arrival_iso' mancante.")
        return
        
    df['arrival_iso'] = pd.to_datetime(df['arrival_iso'], errors='coerce')
    df = df.dropna(subset=['arrival_iso']).set_index('arrival_iso').sort_index()
    
    # Usiamo il delta_seconds come proxy dell'energia (ritardo temporale = anomalia)
    df['energia'] = pd.to_numeric(df['delta_seconds'], errors='coerce').fillna(0).clip(lower=0)
    
    # 1. Raggruppamento in griglia oraria continua
    hourly = pd.DataFrame()
    hourly['numero_eventi'] = df.resample('1H').size().astype(float)
    hourly['energia_max'] = df['energia'].resample('1H').max().fillna(0.0)
    hourly['energia_media'] = df['energia'].resample('1H').mean().fillna(0.0)
    
    # 2. Calcolo Finestre Mobili (Rolling Features)
    hourly['eventi_ultime_6h'] = hourly['numero_eventi'].rolling(6, min_periods=1).sum()
    hourly['eventi_ultime_24h'] = hourly['numero_eventi'].rolling(24, min_periods=1).sum()
    hourly['energia_max_ultime_12h'] = hourly['energia_max'].rolling(12, min_periods=1).max()
    hourly['trend_energia_media'] = hourly['energia_media'].rolling(24, min_periods=1).mean()
    
    # 3. Target dummy per l'inferenza (i dati futuri non sanno ancora se ci sarà allarme)
    hourly['max_energia_futura_24h'] = 0.0
    hourly['Target_Allarme'] = 0
    
    # 4. Ordine rigido colonne per la compatibilità con il modello XGBoost
    cols = ['numero_eventi', 'energia_max', 'energia_media', 'max_energia_futura_24h', 'Target_Allarme', 'eventi_ultime_6h', 'eventi_ultime_24h', 'energia_max_ultime_12h', 'trend_energia_media']
    hourly = hourly[cols]
    hourly.index.name = 'Tempo'
    
    # 5. Integrazione col dataset storico
    if ml_csv.exists():
        df_old = pd.read_csv(ml_csv)
        df_old['Tempo'] = pd.to_datetime(df_old['Tempo'])
        df_old.set_index('Tempo', inplace=True)
        # Sovrascrive le righe in caso di sovrapposizione oraria, tenendo quelle appena calcolate
        combined = pd.concat([df_old, hourly])
        hourly = combined[~combined.index.duplicated(keep='last')].sort_index()
        
    hourly.to_csv(ml_csv)
    logger.info(f"✅ Dataset ML aggiornato e pronto per l'inferenza in: {ml_csv.name}")


def main():
    parser = argparse.ArgumentParser(description="Ingestione continua dati sismici")
    parser.add_argument("--start-date", required=True, help="Data inizio (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="Data fine (YYYY-MM-DD)")
    parser.add_argument("--window-days", type=int, default=1, help="Giorni per singolo batch")
    parser.add_argument("--master-csv", type=Path, default=PROJECT_ROOT / "runs" / "master_deltas.csv")
    parser.add_argument("--ml-dataset", type=Path, default=PROJECT_ROOT / "examples" / "mobile_devices" / "dataset_ml_sismico.csv")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d")
    
    # Assicurati che la directory del master esista
    args.master_csv.parent.mkdir(parents=True, exist_ok=True)
    
    # Directory temporanea di lavoro
    temp_dir = PROJECT_ROOT / "runs" / "temp_ingestion"
    waveforms_dir = temp_dir / "waveforms"
    daily_csv = temp_dir / "daily_deltas.csv"
    
    python_exe = sys.executable
    current_dt = start_dt

    logger.info("Inizio Ingestione Continua...")
    logger.info(f"Periodo: {start_dt.date()} -> {end_dt.date()}")

    while current_dt <= end_dt:
        chunk_end_dt = current_dt + timedelta(days=args.window_days) - timedelta(seconds=1)
        if chunk_end_dt > end_dt:
            chunk_end_dt = end_dt + timedelta(days=1) - timedelta(seconds=1)

        logger.info("-" * 50)
        logger.info(f"Processando finestra: {current_dt} -> {chunk_end_dt}")

        # 0. Pulisci la cartella temporanea (sicurezza)
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        waveforms_dir.mkdir(parents=True, exist_ok=True)

        # 1. DOWNLOAD (Micro-batch)
        logger.info("Fase 1: Download MiniSEED...")
        cmd_download = [
            python_exe, str(PROJECT_ROOT / "scripts" / "download_cf_waveforms.py"),
            "--config", str(args.config),
            "--output-dir", str(waveforms_dir),
            "--start", current_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "--end", chunk_end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        ]
        success = run_command(cmd_download, "Errore durante il download")
        
        if not success or not any(waveforms_dir.iterdir()):
            logger.warning("Nessun dato scaricato in questa finestra. Passo alla successiva.")
            current_dt += timedelta(days=args.window_days)
            continue

        # 2. ELABORAZIONE (Estrazione Delta)
        logger.info("Fase 2: Estrazione metriche STA/LTA dai MiniSEED...")
        cmd_compute = [
            python_exe, str(PROJECT_ROOT / "scripts" / "compute_mseed_deltas.py"),
            "--mseed-dir", str(waveforms_dir),
            "--output-csv", str(daily_csv)
        ]
        success = run_command(cmd_compute, "Errore durante il calcolo dei delta")

        # 3. AGGREGAZIONE (Salvataggio metriche)
        if success and daily_csv.exists():
            logger.info("Fase 3: Aggregazione dati...")
            append_to_master(daily_csv, args.master_csv)
        else:
            logger.warning("Nessuna metrica estratta in questa finestra.")

        # 4. CLEANUP (Rimozione MiniSEED per liberare spazio)
        logger.info("Fase 4: Pulizia file sorgente...")
        try:
            shutil.rmtree(waveforms_dir)
            logger.info("File MiniSEED eliminati con successo dal disco.")
        except Exception as e:
            logger.error(f"Errore durante l'eliminazione dei MiniSEED: {e}")

        # Avanza nel tempo
        current_dt += timedelta(days=args.window_days)

    # FASE 5: Feature Engineering Online (Una volta alla fine del ciclo)
    if args.master_csv.exists():
        update_ml_features(args.master_csv, args.ml_dataset)

    # Pulizia finale cartella temporanea
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    logger.info("=" * 50)
    logger.info("Ingestione Continua Terminata.")
    logger.info(f"Tutti i dati estratti sono stati aggregati in: {args.master_csv}")

if __name__ == "__main__":
    main()