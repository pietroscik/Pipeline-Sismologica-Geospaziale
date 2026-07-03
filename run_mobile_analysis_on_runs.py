#!/usr/bin/env python3
"""
Script per eseguire l'analisi di Machine Learning (mobile analysis)
su un intervallo di esecuzioni della pipeline già completate.

Questo script è utile per addestrare i modelli su dati raccolti
in precedenza tramite `continuous_ingestion.py`, senza dover
rieseguire l'intera pipeline di elaborazione dati.

Usage:
    # Esegue l'analisi mobile su tutte le run di Maggio 2024
    python scripts/run_mobile_analysis_on_runs.py ^
        --start-date 2024-05-01 ^
        --end-date 2024-05-31 ^
        --stations-csv "examples/mobile_devices/stations.csv"
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from scripts.utils import get_project_root, setup_logger

logger = setup_logger("mobile_analysis_runner")

PROJECT_ROOT = get_project_root()


def run_command(cmd: list[str], error_msg: str) -> bool:
    """Esegue un comando subprocess e gestisce gli errori."""
    try:
        # Usiamo un timeout generoso per l'analisi ML
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=3600  # 1 ora
        )
        logger.info(f"Output per {cmd[3]}:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"Stderr per {cmd[3]}:\n{result.stderr}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"{error_msg}. Exit code: {e.returncode}")
        logger.error(f"Dettagli: {e.stderr}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"{error_msg}. Il comando ha superato il timeout.")
        return False


def main():
    parser = argparse.ArgumentParser(description="Esegue l'analisi ML su run esistenti.")
    parser.add_argument("--start-date", required=True, help="Data inizio (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="Data fine (YYYY-MM-DD)")
    parser.add_argument("--stations-csv", required=True, help="Percorso al file CSV delle stazioni.")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d")
    python_exe = sys.executable

    current_dt = start_dt
    while current_dt <= end_dt:
        run_name = f"ingestion_{current_dt.strftime('%Y%m%d')}"
        run_dir = PROJECT_ROOT / "runs" / run_name

        if not run_dir.exists():
            logger.warning(f"Directory di run non trovata, la salto: {run_dir}")
            current_dt += timedelta(days=1)
            continue

        logger.info(f"--- Avvio analisi mobile per la run: {run_name} ---")

        cmd_analysis = [
            python_exe, str(PROJECT_ROOT / "run_pipeline.py"),
            "--run-name", run_name,
            "--stations-csv", args.stations_csv,
            "--mobile-analysis",
            "--skip-phase0", "--skip-phase1", "--skip-phase2", "--skip-phase3", "--skip-phase4"
        ]
        run_command(cmd_analysis, f"Errore durante l'analisi mobile per {run_name}")
        current_dt += timedelta(days=1)

    logger.info("=" * 50)
    logger.info("Analisi mobile su tutte le run specificate completata.")

if __name__ == "__main__":
    main()