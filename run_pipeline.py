#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

sys.path.append(str(PROJECT_ROOT / "scripts"))
from utils import setup_logger

logger = setup_logger("orchestrator")


def run_cmd(cmd_list, optional=False):
    """Esegue un comando. Se optional=True, non lancia eccezione in caso di errore."""
    cmd_str = [str(arg) for arg in cmd_list]
    try:
        subprocess.run(cmd_str, check=True)
        return True
    except subprocess.CalledProcessError as e:
        if optional:
            logger.warning(f"Comando fallito (opzionale): {' '.join(cmd_str)}
Errore: {e.stderr}")
            return False
        else:
            raise


def main():
    parser = argparse.ArgumentParser(
        description="Orchestratore Pipeline Sismologica Geospaziale",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi di uso:
  # Esecuzione completa con dati pre-esistenti
  python run_pipeline.py --run-name mia_analisi
  
  # Esecuzione solo Fasi 3-4 con file delta pre-elaborato
  python run_pipeline.py --run-name mia_analisi --start-phase 3 \
    --delta-csv runs/mia_analisi/processed/station_stats.csv \
    --stations-csv data/raw/stations.csv
  
  # Esecuzione con file personalizzati
  python run_pipeline.py --run-name test \
    --events-csv dati/eventi.csv \
    --picks-csv dati/picks.csv \
    --stations-csv dati/stazioni.csv
"""
    )
    
    parser.add_argument("--run-name", type=str, default=datetime.now().strftime("%Y%m%d_%H%M%S"),
                        help="Nome della cartella di esecuzione (default: timestamp)")
    
    parser.add_argument("--events-csv", type=str, default=None,
                        help="Percorso al file CSV degli eventi")
    parser.add_argument("--picks-csv", type=str, default=None,
                        help="Percorso al file CSV dei picks")
    parser.add_argument("--stations-csv", type=str, default=None,
                        help="Percorso al file CSV delle stazioni")
    parser.add_argument("--delta-csv", type=str, default=None,
                        help="Percorso a file CSV delta pre-elaborato (salta Fasi 0-2)")
    
    parser.add_argument("--start-phase", type=int, choices=[0, 1, 2, 3, 4], default=2,
                        help="Fase di partenza (0-4). Default: 2")
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

    def resolve_path(path_str, default_dir=data_raw_dir):
        if path_str:
            path = Path(path_str)
            if not path.is_absolute():
                path = default_dir / path
            return path
        return None
    
    events_csv = resolve_path(args.events_csv)
    picks_csv = resolve_path(args.picks_csv)
    stations_csv = resolve_path(args.stations_csv)
    delta_csv_input = resolve_path(args.delta_csv, run_dir / "interim")
    
    global_selected_stations = data_raw_dir / "selected_stations.txt"
    selected_stations_txt = run_dir / "selected_stations.txt"

    data_interim_dir.mkdir(parents=True, exist_ok=True)
    data_processed_dir.mkdir(parents=True, exist_ok=True)
    maps_dir.mkdir(parents=True, exist_ok=True)

    if global_selected_stations.exists() and not selected_stations_txt.exists():
        logger.info(f"Importato filtro stazioni da: {global_selected_stations.name}")
        shutil.copy(global_selected_stations, selected_stations_txt)

    python_exe = sys.executable
    out_station_deltas = None
    out_station_stats = None
    out_deltas_spatial = None

    try:
        # FASE 0: Selezione Spaziale
        if args.start_phase <= 0 and not args.skip_phase0:
            logger.info("[Fase 0] Selezione stazioni spaziale...")
            if stations_csv is None:
                logger.warning("Attenzione: --stations-csv non specificato. Fase 0 saltata.")
            else:
                run_cmd([
                    python_exe, scripts_dir / "select_stations_spatial.py",
                    "--input-csv", str(stations_csv),
                    "--output-file", str(selected_stations_txt),
                    "--point", "40.82", "14.14", "20.0"
                ])
                logger.info("Fase 0 completata.")

        # FASE 1: Download
        if args.start_phase <= 1 and not args.skip_phase1:
            logger.info("[Fase 1] Download forme d'onda...")
            if not selected_stations_txt.exists():
                logger.warning("Attenzione: Nessun file stazioni selezionate. Fase 1 saltata.")
            else:
                run_cmd([
                    python_exe, scripts_dir / "download_cf_waveforms.py",
                    "--stations-file", str(selected_stations_txt),
                    "--dry-run"
                ], optional=True)
                logger.info("Fase 1 completata (dry-run).")

        # FASE 2: Elaborazione Delta
        if args.start_phase <= 2 and not args.skip_phase2:
            if args.delta_csv:
                logger.info(f"[Fase 2] Utilizzo file delta pre-esistente: {args.delta_csv}")
                out_station_deltas = Path(args.delta_csv)
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
                    python_exe, scripts_dir / "prepare_science_deltas.py",
                    "--events-csv", str(events_csv),
                    "--picks-csv", str(picks_csv),
                    "--output-csv", str(out_station_deltas)
                ]
                if selected_stations_txt.exists():
                    logger.info(f"  -> Applico filtro spaziale stazioni")
                    cmd_step1.extend(["--stations-file", str(selected_stations_txt)])
                run_cmd(cmd_step1)
            
            logger.info("[Fase 2 - Step 2] Aggregazione statistiche...")
            out_station_stats = data_processed_dir / "station_stats.csv"
            run_cmd([
                python_exe, scripts_dir / "compute_station_stats.py",
                "--base-csv", str(out_station_deltas),
                "--output-csv", str(out_station_stats)
            ])
            logger.info("Fase 2 completata.")
        else:
            if args.delta_csv:
                out_station_deltas = Path(args.delta_csv)
                out_station_stats = data_processed_dir / "station_stats.csv"
                logger.info(f"[Saltata Fase 2] Utilizzo delta: {args.delta_csv}")
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
            
            run_cmd([
                python_exe, scripts_dir / "attach_coords_to_deltas.py",
                "--delta-csv", str(out_station_stats),
                "--stations-csv", str(stations_csv),
                "--output-csv", str(out_deltas_spatial),
                "--value-column", "base_mean"
            ])
            logger.info("Fase 3 completata.")
        else:
            if args.delta_csv:
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
            
            run_cmd([
                python_exe, scripts_dir / "analyze_delta_map.py",
                "--delta-csv", str(out_deltas_spatial),
                "--stats-csv", str(out_station_stats),
                "--outdir", str(maps_dir),
                "--export-geotiff",
                "--export-shapefile",
                "--anomaly-threshold", "0.5"
            ])
            logger.info("Fase 4 completata.")

        logger.info("=" * 60)
        logger.info("  Pipeline completata con successo!")
        logger.info(f"  Risultati in: {run_dir}/")
        logger.info("=" * 60)

    except subprocess.CalledProcessError as e:
        logger.error(f"Errore critico: Comando fallito con codice {e.returncode}.")
        logger.error(f"Comando: {' '.join(e.cmd)}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Errore inaspettato: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
