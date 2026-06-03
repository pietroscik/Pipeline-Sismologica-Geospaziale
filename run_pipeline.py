#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

sys.path.append(str(PROJECT_ROOT / "scripts"))
from utils import setup_logger  # noqa: E402

logger = setup_logger("orchestrator")


def run_cmd(cmd_list: list[object], optional: bool = False) -> bool:
    """Esegue un comando esterno e, se richiesto, lo tratta come opzionale."""
    cmd_str = [str(arg) for arg in cmd_list]
    try:
        subprocess.run(cmd_str, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        if optional:
            logger.warning(f"Comando fallito (opzionale): {' '.join(cmd_str)}")
            logger.warning(f"Errore: {exc}")
            return False
        raise


def resolve_input_path(path_str: str | None, fallback_dirs: list[Path]) -> Path | None:
    """Risolvi un path assoluto, progetto-relative o data-relative."""
    if not path_str:
        return None

    path = Path(path_str)
    if path.is_absolute():
        return path

    root_candidate = PROJECT_ROOT / path
    if root_candidate.exists():
        return root_candidate

    for base_dir in fallback_dirs:
        candidate = base_dir / path
        if candidate.exists():
            return candidate

    if path.parent != Path("."):
        return root_candidate

    if fallback_dirs:
        return fallback_dirs[0] / path
    return root_candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestratore Pipeline Sismologica Geospaziale",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi di uso:
  # Esecuzione completa con dati pre-esistenti
  python run_pipeline.py --run-name mia_analisi

  # Esecuzione con il dataset dimostrativo legacy integrato nel progetto
  python run_pipeline.py --run-name demo_legacy --start-phase 0 \
    --delta-csv examples/mobile_devices/scoperte_automatiche.csv.gz \
    --stations-csv examples/mobile_devices/stations.csv

  # Esecuzione con file personalizzati
  python run_pipeline.py --run-name test \
    --events-csv dati/eventi.csv \
    --picks-csv dati/picks.csv \
    --stations-csv dati/stazioni.csv
"""
    )

    parser.add_argument(
        "--run-name",
        type=str,
        default=datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Nome della cartella di esecuzione (default: timestamp)",
    )

    parser.add_argument("--events-csv", type=str, default=None, help="Percorso al file CSV degli eventi")
    parser.add_argument("--picks-csv", type=str, default=None, help="Percorso al file CSV dei picks")
    parser.add_argument("--stations-csv", type=str, default=None, help="Percorso al file CSV delle stazioni")
    parser.add_argument(
        "--delta-csv",
        type=str,
        default=None,
        help="Percorso a file CSV delta pre-elaborato (salta Fasi 0-2)",
    )

    parser.add_argument(
        "--start-phase",
        type=int,
        choices=[0, 1, 2, 3, 4],
        default=2,
        help="Fase di partenza (0-4). Default: 2",
    )
    parser.add_argument("--skip-phase0", action="store_true", help="Salta Fase 0")
    parser.add_argument("--skip-phase1", action="store_true", help="Salta Fase 1")
    parser.add_argument("--skip-phase2", action="store_true", help="Salta Fase 2")
    parser.add_argument("--skip-phase3", action="store_true", help="Salta Fase 3")
    parser.add_argument("--skip-phase4", action="store_true", help="Salta Fase 4")

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("  Pipeline Analisi Dati Sismici Geospaziale")
    logger.info(f"  Esecuzione ID: {args.run_name}")
    logger.info(f"  Fase di partenza: {args.start_phase}")
    logger.info("=" * 60)

    scripts_dir = PROJECT_ROOT / "scripts"
    data_raw_dir = PROJECT_ROOT / "data" / "raw"
    run_dir = PROJECT_ROOT / "runs" / args.run_name
    data_interim_dir = run_dir / "interim"
    data_processed_dir = run_dir / "processed"
    maps_dir = run_dir / "maps"

    if run_dir.exists():
        logger.info(f"Pulizia parziale della cartella: {run_dir}")
        for sub_dir in [data_interim_dir, data_processed_dir, maps_dir]:
            if sub_dir.exists():
                shutil.rmtree(sub_dir)

    events_csv = resolve_input_path(args.events_csv, [data_raw_dir])
    picks_csv = resolve_input_path(args.picks_csv, [data_raw_dir])
    stations_csv = resolve_input_path(args.stations_csv, [data_raw_dir])
    delta_csv = resolve_input_path(
        args.delta_csv,
        [run_dir / "processed", run_dir / "interim", data_raw_dir],
    )

    global_selected_stations = data_raw_dir / "selected_stations.txt"
    selected_stations_txt = run_dir / "selected_stations.txt"

    data_interim_dir.mkdir(parents=True, exist_ok=True)
    data_processed_dir.mkdir(parents=True, exist_ok=True)
    maps_dir.mkdir(parents=True, exist_ok=True)

    if global_selected_stations.exists() and not selected_stations_txt.exists():
        logger.info(f"Importato filtro stazioni da: {global_selected_stations.name}")
        shutil.copy(global_selected_stations, selected_stations_txt)

    python_exe = sys.executable
    out_station_deltas: Path | None = None
    out_station_stats: Path | None = None
    out_deltas_spatial: Path | None = None

    try:
        # FASE 0: Selezione Spaziale
        if args.start_phase <= 0 and not args.skip_phase0:
            logger.info("[Fase 0] Selezione stazioni spaziale...")
            if stations_csv is None:
                logger.warning("Attenzione: --stations-csv non specificato. Fase 0 saltata.")
            else:
                run_cmd(
                    [
                        python_exe,
                        scripts_dir / "select_stations_spatial.py",
                        "--input-csv",
                        str(stations_csv),
                        "--output-file",
                        str(selected_stations_txt),
                        "--point",
                        "40.82",
                        "14.14",
                        "20.0",
                    ]
                )
                logger.info("Fase 0 completata.")

        # FASE 1: Download
        if args.start_phase <= 1 and not args.skip_phase1:
            logger.info("[Fase 1] Download forme d'onda...")
            if not selected_stations_txt.exists():
                logger.warning("Attenzione: Nessun file stazioni selezionate. Fase 1 saltata.")
            else:
                run_cmd(
                    [
                        python_exe,
                        scripts_dir / "download_cf_waveforms.py",
                        "--stations-file",
                        str(selected_stations_txt),
                        "--dry-run",
                    ],
                    optional=True,
                )
                logger.info("Fase 1 completata (dry-run).")

        # FASE 2: Elaborazione Delta
        if args.start_phase <= 2 and not args.skip_phase2:
            if delta_csv:
                logger.info(f"[Fase 2] Utilizzo file delta pre-esistente: {delta_csv}")
                out_station_deltas = delta_csv
                if not out_station_deltas.exists():
                    logger.error(f"File delta non trovato: {out_station_deltas}")
                    sys.exit(1)
            else:
                logger.info("[Fase 2 - Step 1] Preparazione delta dai cataloghi...")
                out_station_deltas = data_interim_dir / "station_deltas.csv"

                if events_csv is None or picks_csv is None:
                    logger.error("Per Fase 2 servono --events-csv e --picks-csv o --delta-csv.")
                    sys.exit(1)

                cmd_step1 = [
                    python_exe,
                    scripts_dir / "prepare_science_deltas.py",
                    "--events-csv",
                    str(events_csv),
                    "--picks-csv",
                    str(picks_csv),
                    "--output-csv",
                    str(out_station_deltas),
                ]
                if selected_stations_txt.exists():
                    logger.info("  -> Applico filtro spaziale stazioni")
                    cmd_step1.extend(["--stations-file", str(selected_stations_txt)])
                run_cmd(cmd_step1)

            logger.info("[Fase 2 - Step 2] Aggregazione statistiche...")
            out_station_stats = data_processed_dir / "station_stats.csv"
            cmd_stats = [
                python_exe,
                scripts_dir / "compute_station_stats.py",
                "--base-csv",
                str(out_station_deltas),
                "--output-csv",
                str(out_station_stats),
            ]
            if selected_stations_txt.exists():
                cmd_stats.extend(["--stations-file", str(selected_stations_txt)])
            run_cmd(cmd_stats)
            logger.info("Fase 2 completata.")
        else:
            if delta_csv:
                out_station_deltas = delta_csv
                out_station_stats = data_processed_dir / "station_stats.csv"
                logger.info(f"[Saltata Fase 2] Utilizzo delta: {delta_csv}")
            else:
                logger.error("Per saltare Fase 2, serve --delta-csv.")
                sys.exit(1)

        # FASE 3: Spazializzazione
        if args.start_phase <= 3 and not args.skip_phase3:
            logger.info("[Fase 3] Integrazione coordinate metriche...")
            out_deltas_spatial = data_processed_dir / "deltas_spatial.csv"

            if stations_csv is None:
                logger.error("Per Fase 3 serve --stations-csv.")
                sys.exit(1)

            run_cmd(
                [
                    python_exe,
                    scripts_dir / "attach_coords_to_deltas.py",
                    "--delta-csv",
                    str(out_station_stats),
                    "--stations-csv",
                    str(stations_csv),
                    "--output-csv",
                    str(out_deltas_spatial),
                    "--value-column",
                    "base_mean",
                ]
            )
            logger.info("Fase 3 completata.")
        else:
            if delta_csv:
                out_deltas_spatial = data_processed_dir / "deltas_spatial.csv"
                if not out_deltas_spatial.exists():
                    logger.warning(f"Fase 3 saltata. Assicurati che {out_deltas_spatial} esista.")

        # FASE 4: Output GIS
        if args.start_phase <= 4 and not args.skip_phase4:
            logger.info("[Fase 4] Generazione output GIS...")

            if out_deltas_spatial is None or not out_deltas_spatial.exists():
                logger.error(f"File delta spaziale mancante: {out_deltas_spatial}")
                sys.exit(1)

            if out_station_stats is None or not out_station_stats.exists():
                out_station_stats = data_processed_dir / "station_stats.csv"
                if not out_station_stats.exists():
                    logger.error("File statistiche stazioni mancante.")
                    sys.exit(1)

            run_cmd(
                [
                    python_exe,
                    scripts_dir / "analyze_delta_map.py",
                    "--delta-csv",
                    str(out_deltas_spatial),
                    "--stats-csv",
                    str(out_station_stats),
                    "--outdir",
                    str(maps_dir),
                    "--export-geotiff",
                    "--export-shapefile",
                    "--anomaly-threshold",
                    "0.5",
                ]
            )
            logger.info("Fase 4 completata.")

        logger.info("=" * 60)
        logger.info("  Pipeline completata con successo!")
        logger.info(f"  Risultati in: {run_dir}/")
        logger.info("=" * 60)

    except subprocess.CalledProcessError as exc:
        logger.error(f"Errore critico: Comando fallito con codice {exc.returncode}.")
        logger.error(f"Comando: {' '.join(exc.cmd)}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Errore inaspettato: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
