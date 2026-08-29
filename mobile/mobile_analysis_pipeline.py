#!/usr/bin/env python3
"""Orchestrate the mobile seismic-analysis workflow.

The workflow keeps legacy analysis scripts isolated while exposing one stable
CLI for scheduled jobs and the dashboard.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "examples" / "mobile_devices"
DEFAULT_TIMEOUT = 600

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("mobile_pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline unificata per analisi mobile e generazione allarmi"
    )
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--stations-csv", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "runs" / "mobile_analysis"
    )
    parser.add_argument("--min-stations", type=int, default=18)
    parser.add_argument("--alert-threshold", type=float, default=0.7)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--sequence-length", type=int, default=10)
    parser.add_argument(
        "--model-type",
        choices=["compare", "xgboost", "random_forest", "transformer"],
        default="compare",
    )
    parser.add_argument("--generate-alerts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--cleanup-on-error", action="store_true")
    return parser.parse_args()


def _run(command: list[str], *, cwd: Path, timeout: int, dry_run: bool) -> None:
    logger.info("%s%s", "[dry-run] " if dry_run else "", " ".join(command))
    if not dry_run:
        subprocess.run(command, cwd=cwd, check=True, timeout=timeout)


def _validate_inputs(args: argparse.Namespace) -> None:
    for path in (args.input_csv, args.stations_csv):
        if not path.is_file():
            raise FileNotFoundError(f"File di input non trovato: {path}")
    if not 0 < args.alert_threshold <= 1:
        raise ValueError("--alert-threshold deve essere compreso tra 0 e 1")
    if args.min_stations < 1 or args.epochs < 1 or args.timeout < 1:
        raise ValueError("min-stations, epochs e timeout devono essere positivi")


def _commands(args: argparse.Namespace) -> list[tuple[list[str], int]]:
    python = sys.executable
    return [
        (
            [
                python,
                str(SCRIPTS_DIR / "process_pipeline.py"),
                "--input-csv",
                str(args.input_csv),
                "--stations-csv",
                str(args.stations_csv),
                "--output-dir",
                str(args.output_dir / "interim"),
            ],
            args.timeout,
        ),
        (
            [
                python,
                str(SCRIPTS_DIR / "associa_eventi.py"),
                "--input-file",
                str(args.output_dir / "interim" / "output_eventi_georeferenziati.csv.gz"),
                "--output-file",
                str(args.output_dir / "processed" / "catalogo_terremoti_unici.csv")
            ],
            args.timeout,
        ),
        (
            [
                python,
                str(SCRIPTS_DIR / "prepara_ml.py"),
                "--input-csv",
                str(args.output_dir / "interim" / "output_eventi_georeferenziati.csv.gz"),
                "--catalogo-csv",
                str(args.output_dir / "processed" / "catalogo_terremoti_unici.csv"),
                "--stations-csv",
                str(args.stations_csv),
                "--output-dir",
                str(args.output_dir / "processed"),
            ],
            args.timeout * 2,
        ),
        (
            [
                python,
                str(SCRIPTS_DIR / "train_modello.py"),
                "--model-type",
                args.model_type,
                "--model-output-dir",
                str(args.output_dir / "models"),
                "--epochs",
                str(args.epochs),
                "--learning-rate",
                str(args.learning_rate),
                "--sequence-length",
                str(args.sequence_length),
                "--min-stations",
                str(args.min_stations),
                "--alert-threshold",
                str(args.alert_threshold),
            ]
            + (["--generate-alerts"] if args.generate_alerts else []),
            args.timeout * 3,
        ),
    ]


def main() -> int:
    args = parse_args()
    try:
        _validate_inputs(args)
        if not SCRIPTS_DIR.is_dir():
            raise FileNotFoundError(f"Directory script non trovata: {SCRIPTS_DIR}")

        if args.dry_run:
            logger.info("Pipeline mobile: simulazione")
            for command, timeout in _commands(args):
                _run(command, cwd=SCRIPTS_DIR, timeout=timeout, dry_run=True)
            return 0

        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name in ("interim", "processed", "output", "models", "alerts", "logs"):
            (args.output_dir / name).mkdir(exist_ok=True)
        shutil.copy2(args.input_csv, args.output_dir / args.input_csv.name)
        shutil.copy2(args.stations_csv, args.output_dir / args.stations_csv.name)

        for command, timeout in _commands(args):
            _run(command, cwd=SCRIPTS_DIR, timeout=timeout, dry_run=False)
        logger.info("Pipeline mobile completata: %s", args.output_dir)
        return 0
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        logger.error("Pipeline mobile fallita: %s", exc)
        if args.cleanup_on_error and args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())