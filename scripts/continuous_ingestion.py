#!/usr/bin/env python3
"""
Script per l'ingestione continua e l'elaborazione dei dati sismici.

Implementa la strategia di Micro-Batching: Scarica -> Estrai Metriche -> Salva -> Elimina.
Questo script orchestra `run_pipeline.py` per eseguire l'analisi su piccoli
intervalli temporali (tipicamente giornalieri) e ingerire i risultati nel database.

Usage:
    # Esegue l'ingestione per tutto il mese di Maggio 2024, un giorno alla volta
    python scripts/continuous_ingestion.py ^
        --start-date 2024-05-01 ^
        --end-date 2024-05-31 ^
        --stations-csv "examples/mobile_devices/stations.csv" ^
        --auto-ingest
"""

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.utils import get_project_root, setup_logger

logger = setup_logger("continuous_ingestion")

PROJECT_ROOT = get_project_root()


def run_command(cmd: list[str], error_msg: str) -> bool:
    """Esegue un comando subprocess e gestisce gli errori."""
    try:
        # Usiamo un timeout generoso per ogni fase
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=1800
        )  # 30 min timeout
        if result.stdout:
            logger.debug(f"Output: {result.stdout[:500]}")
        if result.stderr:
            logger.warning(f"Stderr: {result.stderr[:500]}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"{error_msg}. Exit code: {e.returncode}")
        logger.error(f"Dettagli: {e.stderr}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"{error_msg}. Il comando ha superato il timeout.")
        return False


def main():
    parser = argparse.ArgumentParser(description="Ingestione continua dati sismici")
    parser.add_argument("--start-date", required=True, help="Data inizio (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="Data fine (YYYY-MM-DD)")
    parser.add_argument(
        "--window-days", type=int, default=1, help="Giorni per singolo batch"
    )
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    # Argomenti per passare i file di input alla pipeline
    parser.add_argument(
        "--stations-csv",
        type=str,
        required=True,
        help="Percorso al file CSV delle stazioni (obbligatorio).",
    )
    # Nuovo argomento per l'ingestione automatica
    parser.add_argument(
        "--auto-ingest",
        action="store_true",
        help="Esegue automaticamente l'ingestione dei risultati nel database DuckDB.",
    )

    args = parser.parse_args()

    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d")

    python_exe = sys.executable

    current_dt = start_dt
    logger.info("Inizio Ingestione Continua...")
    logger.info(f"Periodo: {current_dt.date()} -> {end_dt.date()}")

    while current_dt <= end_dt:
        chunk_end_dt = (
            current_dt + timedelta(days=args.window_days) - timedelta(seconds=1)
        )
        if chunk_end_dt > end_dt:
            chunk_end_dt = end_dt + timedelta(days=1) - timedelta(seconds=1)

        run_name = f"ingestion_{current_dt.strftime('%Y%m%d')}"
        run_dir = PROJECT_ROOT / "runs" / run_name

        logger.info("-" * 50)
        logger.info(
            f"Processando finestra: {current_dt.date()} -> {chunk_end_dt.date()} in {run_dir}"
        )

        # 1. Esegui la pipeline completa per la finestra temporale
        cmd_pipeline = [
            python_exe,
            str(PROJECT_ROOT / "run_pipeline.py"),
            "--run-name",
            run_name,
            "--run-download",
            "--download-start",
            current_dt.strftime("%Y-%m-%d"),
            "--download-end",
            chunk_end_dt.strftime("%Y-%m-%d"),
            "--stations-csv",
            args.stations_csv,
            "--start-phase",
            "0",  # Parti sempre da zero
        ]
        # Aggiungi l'ingestione automatica se richiesta
        if args.auto_ingest:
            cmd_pipeline.append("--auto-ingest")

        success = run_command(
            cmd_pipeline, f"Errore durante l'esecuzione della pipeline per {run_name}"
        )

        if not success:
            logger.warning(
                f"La pipeline per la finestra {current_dt.date()} è fallita. Passo alla successiva."
            )
            current_dt += timedelta(days=args.window_days)
            continue

        # 2. CLEANUP INTELLIGENTE: Rimuovi solo i dati grezzi (waveforms) per liberare spazio,
        # ma conserva i risultati (interim, processed, maps).
        waveforms_dir = run_dir / "waveforms"
        logger.info(f"Pulizia dei dati grezzi: {waveforms_dir}")
        try:
            if waveforms_dir.exists():
                shutil.rmtree(waveforms_dir)
                logger.info("Directory waveforms eliminata con successo.")
            else:
                logger.info("Nessuna directory waveforms da eliminare.")
        except Exception as e:
            logger.error(f"Errore durante l'eliminazione della directory waveforms: {e}")

        # Avanza nel tempo
        current_dt += timedelta(days=args.window_days)

    logger.info("=" * 50)
    logger.info("Ingestione Continua Terminata.")
    if args.auto_ingest:
        db_path = PROJECT_ROOT / "data" / "db" / "seismic_output.duckdb"
        logger.info(f"Tutti i dati sono stati ingeriti nel database: {db_path}")


if __name__ == "__main__":
    main()
