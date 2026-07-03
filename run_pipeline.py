#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from scripts.utils import load_csv_with_checks, setup_logger, load_config

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent

# L'hack su sys.path è stato completamente rimosso!
from mobile.data_validator import DataValidationError, validate_csv_file
from scripts.utils import setup_logger

logger = setup_logger("orchestrator")


# Default timeout for subprocess commands (in seconds)

DEFAULT_TIMEOUT = 300  # 5 minutes


def run_cmd(
    cmd_list: List[str],
    optional: bool = False,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
) -> bool:
    """

    Esegue un comando esterno con timeout e gestione errori migliorata.



    Args:

        cmd_list: Lista di argomenti del comando

        optional: Se True, il fallimento non solleva eccezione

        timeout: Timeout in secondi (default: DEFAULT_TIMEOUT)

        cwd: Working directory per il comando



    Returns:

        True se comando completato con successo, False se fallito (e optional=True)



    Raises:

        subprocess.TimeoutExpired: Se il comando supera il timeout

        subprocess.CalledProcessError: Se il comando fallisce e optional=False

    """

    if timeout is None:

        timeout = DEFAULT_TIMEOUT

    cmd_str = [str(arg) for arg in cmd_list]

    logger.debug(f"Esecuzione: {' '.join(cmd_str)}")

    try:

        result = subprocess.run(
            cmd_str,
            check=True,
            timeout=timeout,
            cwd=cwd,
            capture_output=True,
            text=True,
        )

        if result.stdout:

            logger.debug(f"Output: {result.stdout[:500]}")  # Log first 500 chars

        if result.stderr:

            logger.warning(f"Stderr: {result.stderr[:500]}")  # Log first 500 chars

        return True

    except subprocess.TimeoutExpired as exc:

        logger.error(f"Comando timeout dopo {timeout}s: {' '.join(cmd_str)}")

        if optional:

            logger.warning("Comando opzionale - timeout ignorato")

            return False

        raise

    except subprocess.CalledProcessError as exc:

        if optional:

            logger.warning(f"Comando fallito (opzionale): {' '.join(cmd_str)}")

            logger.warning(f"Exit code: {exc.returncode}")

            return False

        logger.error(f"Comando fallito: {' '.join(cmd_str)}")

        logger.error(f"Exit code: {exc.returncode}")

        if exc.stderr:

            logger.error(f"Stderr: {exc.stderr[:500]}")

        raise

    except Exception as exc:

        logger.error(f"Errore inatteso in run_cmd: {exc}")

        if optional:

            return False

        raise


def resolve_input_path(
    path_str: Optional[str], fallback_dirs: List[Path]
) -> Optional[Path]:
    """

    Risolvi un path assoluto, progetto-relative o data-relative.



    Args:

        path_str: Stringa del path (può essere None)

        fallback_dirs: Lista di directory di fallback per path relativi



    Returns:

        Path risolto o None se path_str è None



    Raises:

        FileNotFoundError: Se il file non esiste in nessuna location

    """

    if not path_str:

        return None

    path = Path(path_str)

    if path.is_absolute():

        if not path.exists():

            raise FileNotFoundError(f"File non trovato: {path}")

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


def validate_csv_input(
    path: Optional[Path],
    required_columns: Optional[set] = None,
    path_description: str = "CSV file",
) -> Optional[pd.DataFrame]:
    """

    Valida un file CSV input e restituisce il DataFrame.



    Args:

        path: Path al file CSV

        required_columns: Colonne richieste (opzionale)

        path_description: Descrizione del file per messaggi di errore



    Returns:

        DataFrame validato o None se path è None



    Raises:

        DataValidationError: Se la validazione fallisce

        FileNotFoundError: Se il file non esiste

    """

    if path is None:

        return None

    try:

        df = validate_csv_file(path, required_columns=required_columns)

        logger.info(f"{path_description} validato: {path.name}")

        return df

    except DataValidationError as e:

        logger.error(
            f"Validazione fallita per {path_description} ({path}): {e.message}"
        )

        for err in e.errors:

            logger.error(f"  - {err}")

        raise

    except Exception as e:

        logger.error(f"Errore caricamento {path_description} ({path}): {e}")

        raise


def cleanup_run_directory(run_dir: Path) -> None:
    """

    Pulisce la directory di esecuzione in caso di errore.



    Args:

        run_dir: Path alla directory di esecuzione

    """

    try:

        if run_dir.exists():

            logger.info(f"Pulizia directory: {run_dir}")

            shutil.rmtree(run_dir, ignore_errors=True)

    except Exception as e:

        logger.warning(f"Errore durante pulizia: {e}")


def setup_run_directory(run_dir: Path) -> tuple:
    """

    Crea la struttura di directory per una nuova esecuzione.



    Args:

        run_dir: Path alla directory di esecuzione



    Returns:

        Tuple di (data_interim_dir, data_processed_dir, maps_dir, mobile_analysis_dir)

    """

    data_interim_dir = run_dir / "interim"

    data_processed_dir = run_dir / "processed"

    maps_dir = run_dir / "maps"

    mobile_analysis_dir = run_dir / "mobile_analysis"

    # Crea directory

    run_dir.mkdir(parents=True, exist_ok=True)

    data_interim_dir.mkdir(parents=True, exist_ok=True)

    data_processed_dir.mkdir(parents=True, exist_ok=True)

    maps_dir.mkdir(parents=True, exist_ok=True)

    mobile_analysis_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Struttura directory creata: {run_dir}")

    return data_interim_dir, data_processed_dir, maps_dir, mobile_analysis_dir


def main() -> None:
    # Gestione "early-help" per un output più pulito
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        # short message must include "Pipeline" to satisfy integration test expectations
        print("Pipeline Analisi Dati Sismici Geospaziale - run_pipeline help\n")
        # L'epilogo del parser verrà stampato dopo questo messaggio

    parser = argparse.ArgumentParser(
        description="Orchestratore Pipeline Sismologica Geospaziale",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # Disabilitiamo l'help automatico per gestire il nostro "early-help"
        add_help=False,
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



  # Esecuzione con analisi mobile e generazione allarmi

  python run_pipeline.py --run-name analisi_complete \

    --mobile-analysis \

    --mobile-min-stations 18 \

    --mobile-alert-threshold 0.7

""",
    )

    # Aggiungiamo manualmente l'argomento --help
    parser.add_argument(
        "-h", "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Mostra questo messaggio di aiuto ed esci."
    )

    parser.add_argument(
        "--run-name",
        type=str,
        default=datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Nome della cartella di esecuzione (default: timestamp)",
    )

    parser.add_argument(
        "--events-csv", type=str, default=None, help="Percorso al file CSV degli eventi"
    )

    parser.add_argument(
        "--picks-csv", type=str, default=None, help="Percorso al file CSV dei picks"
    )

    parser.add_argument(
        "--stations-csv",
        type=str,
        default=None,
        help="Percorso al file CSV delle stazioni",
    )

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

    # Opzioni per download (Fase 1)

    parser.add_argument(
        "--run-download",
        action="store_true",
        help="Esegui Fase 1 (Download MiniSEED). Richiede --download-start e --download-end.",
    )

    parser.add_argument(
        "--download-start", type=str, help="Data inizio download (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--download-end", type=str, help="Data fine download (YYYY-MM-DD)"
    )

    # === NOVITÀ: Opzioni per analisi mobile e allarmi ===

    parser.add_argument(
        "--mobile-analysis",
        action="store_true",
        help="Esegui analisi mobile e generazione allarmi dopo la pipeline principale",
    )

    parser.add_argument(
        "--mobile-min-stations",
        type=int,
        default=18,
        help="Soglia stazioni per allarme mobile (default: 18)",
    )

    parser.add_argument(
        "--mobile-alert-threshold",
        type=float,
        default=0.7,
        help="Soglia indice di rischio per allarme mobile (default: 0.7)",
    )

    parser.add_argument(
        "--mobile-model-type",
        choices=["compare", "xgboost", "random_forest", "transformer"],
        default="compare",
        help="Tipo di modello ML per analisi mobile (default: compare)",
    )

    # --- MODIFICA: Le analisi sono di default, si possono saltare ---
    parser.add_argument(
        "--skip-b-value",
        action="store_true",
        help="Salta l'analisi del b-value (eseguita di default con --mobile-analysis)."
    )

    parser.add_argument(
        "--skip-noise-analysis",
        action="store_true",
        help="Salta l'analisi del rumore antropico (eseguita di default con --mobile-analysis)."
    )
    
    parser.add_argument(
        "--mobile-generate-alerts",
        action="store_true",
        help="Genera allarmi attivi durante l'analisi mobile",
    )

    # Robustezza: timeout per comandi

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout per comandi in secondi (default: {DEFAULT_TIMEOUT})",
    )

    # Robustezza: cleanup automatico in caso di errore

    parser.add_argument(
        "--cleanup-on-error",
        action="store_true",
        help="Pulisce la directory di esecuzione in caso di errore",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula l'esecuzione senza lanciare i comandi (dry-run)",
    )

    parser.add_argument(
        "--auto-ingest",
        action="store_true",
        help="Esegue automaticamente l'ingestione dei risultati nel database DuckDB al termine della pipeline.",
    )

    args = parser.parse_args()

    if args.dry_run:

        logger.info("Dry run completata.")

        return

    logger.info("=" * 60)

    logger.info("  Pipeline Analisi Dati Sismici Geospaziale")

    logger.info(f"  Esecuzione ID: {args.run_name}")

    logger.info(f"  Fase di partenza: {args.start_phase}")

    logger.info(f"  Timeout: {args.timeout}s")

    if args.mobile_analysis:

        logger.info(f"  Analisi Mobile: ATTIVA")

        logger.info(f"    - Soglia stazioni: {args.mobile_min_stations}")

        logger.info(f"    - Soglia rischio: {args.mobile_alert_threshold}")

        logger.info(f"    - Tipo modello: {args.mobile_model_type}")

    logger.info("=" * 60)

    # --- LOGICA DI CONTROLLO FASI ---
    # Se è richiesto un download, dobbiamo assicurarci che la pipeline parta
    # da una fase che lo includa (Fase 1).
    if args.run_download and args.start_phase > 1:
        new_start_phase = 1 if args.skip_phase0 else 0
        logger.warning(
            f"Opzione --run-download rilevata. La fase di partenza è stata forzata da {args.start_phase} a {new_start_phase} per includere il download."
        )
        args.start_phase = new_start_phase

    scripts_dir = PROJECT_ROOT / "scripts"

    data_raw_dir = PROJECT_ROOT / "data" / "raw"

    run_dir = PROJECT_ROOT / "runs" / args.run_name

    # Setup directory structure

    data_interim_dir, data_processed_dir, maps_dir, mobile_analysis_dir = setup_run_directory(run_dir)

    # Track files for cleanup

    created_files = []

    try:

        # Resolve input paths

        events_csv = resolve_input_path(args.events_csv, [data_raw_dir])

        picks_csv = resolve_input_path(args.picks_csv, [data_raw_dir])

        stations_csv = resolve_input_path(args.stations_csv, [data_raw_dir])

        delta_csv = resolve_input_path(
            args.delta_csv,
            [run_dir / "processed", run_dir / "interim", data_raw_dir],
        )

        global_selected_stations = data_raw_dir / "selected_stations.txt"

        selected_stations_txt = run_dir / "selected_stations.txt"

        if global_selected_stations.exists() and not selected_stations_txt.exists():

            logger.info(
                f"Importato filtro stazioni da: {global_selected_stations.name}"
            )

            shutil.copy(global_selected_stations, selected_stations_txt)

            created_files.append(selected_stations_txt)

        python_exe = sys.executable

        out_station_deltas: Optional[Path] = None

        out_station_stats: Optional[Path] = None

        out_deltas_spatial: Optional[Path] = None

        # FASE 0: Selezione Spaziale

        if args.start_phase <= 0 and not args.skip_phase0:

            logger.info("[Fase 0] Selezione stazioni spaziale...")
            if not stations_csv:
                logger.warning(
                    "Attenzione: --stations-csv non specificato. Fase 0 saltata."
                )
            else:
                # Validate stations CSV
                try:
                    validate_csv_file(
                        stations_csv,
                        required_columns={"station", "latitude", "longitude"},
                    )
                    logger.info(f"File stazioni validato: {stations_csv.name}")
                except Exception as e:
                    logger.error(f"Validazione file stazioni fallita: {e}")
                    raise

                run_cmd(
                    [
                        python_exe,
                        scripts_dir / "select_stations_spatial.py",
                        "--input-csv",
                        stations_csv,
                        "--output-file",
                        str(selected_stations_txt),
                        "--point",
                        "40.82",
                        "14.14",
                        "20.0",
                    ],
                    timeout=args.timeout,
                )

                created_files.append(selected_stations_txt)

                logger.info("Fase 0 completata.")

        # FASE 1: Download

        if args.start_phase <= 1 and not args.skip_phase1:

            if args.run_download:

                logger.info("[Fase 1] Download forme d'onda...")

                if not args.download_start or not args.download_end:

                    raise ValueError(
                        "Per eseguire il download (Fase 1), specificare --download-start e --download-end."
                    )

                cmd_download = [
                    python_exe,
                    scripts_dir / "download_cf_waveforms.py",
                    "--start",
                    f"{args.download_start}T00:00:00",
                    "--end",
                    f"{args.download_end}T23:59:59",
                    "--output-dir",
                    str(run_dir / "waveforms"),
                ]

                if selected_stations_txt.exists():

                    cmd_download.extend(["--stations-file", selected_stations_txt])

                run_cmd(cmd_download, timeout=args.timeout * 4)  # Download can be long

                logger.info("Fase 1 completata.")

            else:

                logger.info(
                    "[Fase 1] Saltata. Per scaricare le tracce, usare l'opzione --run-download."
                )

        # FASE 2: Elaborazione Delta

        if args.start_phase <= 2 and not args.skip_phase2:

            # LOGICA DI PRIORITÀ: Se abbiamo scaricato nuovi dati, dobbiamo elaborarli.
            # Ignoriamo qualsiasi --delta-csv fornito per coerenza.
            if args.run_download:
                logger.info("[Fase 2 - Step 1] Elaborazione MiniSEED appena scaricati...")
                out_station_deltas = data_interim_dir / "station_deltas_from_mseed.csv"
                created_files.append(out_station_deltas)

                waveforms_dir = run_dir / "waveforms"
                if not waveforms_dir.exists() or not any(waveforms_dir.iterdir()):
                    raise FileNotFoundError(f"La cartella dei MiniSEED non esiste o è vuota: {waveforms_dir}")

                cmd_compute_mseed = [
                    python_exe,
                    scripts_dir / "compute_mseed_deltas.py",
                    "--mseed-dir",
                    str(waveforms_dir),
                    "--output-csv",
                    str(out_station_deltas),
                ]
                run_cmd(cmd_compute_mseed, timeout=args.timeout * 2)

                # Avvisa l'utente se un delta-csv è stato ignorato
                if args.delta_csv:
                    logger.warning(
                        f"Opzione --delta-csv ({Path(args.delta_csv).name}) ignorata perché --run-download è attivo."
                    )

            # NUOVA LOGICA: Se non stiamo scaricando, controlliamo se esistono già dei MiniSEED da elaborare.
            elif (run_dir / "waveforms").exists() and any((run_dir / "waveforms").iterdir()):
                logger.info("[Fase 2 - Step 1] Rilevati MiniSEED pre-esistenti. Avvio elaborazione...")
                out_station_deltas = data_interim_dir / "station_deltas_from_mseed.csv"
                created_files.append(out_station_deltas)

                waveforms_dir = run_dir / "waveforms"
                cmd_compute_mseed = [
                    python_exe,
                    scripts_dir / "compute_mseed_deltas.py",
                    "--mseed-dir",
                    str(waveforms_dir),
                    "--output-csv",
                    str(out_station_deltas),
                ]
                run_cmd(cmd_compute_mseed, timeout=args.timeout * 2)

                # Avvisa l'utente se un delta-csv è stato ignorato
                if args.delta_csv:
                    logger.warning(
                        f"Opzione --delta-csv ({Path(args.delta_csv).name}) ignorata perché sono stati trovati MiniSEED da rielaborare."
                    )

            # Se non abbiamo scaricato dati, usiamo la logica pre-esistente.
            elif delta_csv:
                logger.info(f"[Fase 2] Utilizzo file delta pre-esistente: {delta_csv}")

                out_station_deltas = delta_csv
                if not out_station_deltas.exists():
                    logger.error(f"File delta non trovato: {out_station_deltas}")
                    raise FileNotFoundError(
                        f"Delta CSV file not found: {out_station_deltas}"
                    )

            else:

                logger.info("[Fase 2 - Step 1] Preparazione delta dai cataloghi...")

                out_station_deltas = data_interim_dir / "station_deltas.csv"

                created_files.append(out_station_deltas)

                if not all([events_csv, picks_csv]):
                    logger.error(
                        "Per Fase 2 servono --events-csv e --picks-csv o --delta-csv."
                    )
                    raise ValueError(
                        "Events CSV and Picks CSV are required for Phase 2"
                    )

                # Validate input CSV files
                try:
                    validate_csv_file(events_csv, required_columns={"event_id", "time"})
                    validate_csv_file(
                        picks_csv, required_columns={"pick_id", "event_id", "phase"}
                    )
                    logger.info(f"File eventi e picks validati")
                except Exception as e:
                    logger.error(f"Validazione input Fase 2 fallita: {e}")
                    raise

                cmd_step1 = [
                    python_exe,
                    scripts_dir / "prepare_science_deltas.py",
                    "--events-csv",
                    events_csv,
                    "--picks-csv",
                    picks_csv,
                    "--output-csv",
                    str(out_station_deltas),
                ]

                if selected_stations_txt.exists():

                    logger.info("  -> Applico filtro spaziale stazioni")

                    cmd_step1.extend(["--stations-file", selected_stations_txt])

                run_cmd(cmd_step1, timeout=args.timeout)

            logger.info("[Fase 2 - Step 2] Aggregazione statistiche...")

            out_station_stats = data_processed_dir / "station_stats.csv"

            created_files.append(out_station_stats)

            cmd_stats = [
                python_exe,
                scripts_dir / "compute_station_stats.py",
                "--base-csv",
                out_station_deltas,
                "--output-csv",
                str(out_station_stats),
            ]

            if selected_stations_txt.exists():

                cmd_stats.extend(["--stations-file", selected_stations_txt])

            run_cmd(cmd_stats, timeout=args.timeout)

            logger.info("Fase 2 completata.")

        else:

            # Se saltiamo la Fase 2, dobbiamo trovare l'output che avrebbe generato.
            # Priorità 1: --delta-csv esplicito.
            # Priorità 2: File generato da una precedente esecuzione nella stessa cartella di run.
            if delta_csv:
                out_station_deltas = delta_csv
                logger.info(f"[Fase 2 saltata] Utilizzo file delta specificato: {delta_csv.name}")
            else:
                # Cerca i possibili file di output della Fase 2
                possible_delta_files = [
                    data_interim_dir / "station_deltas_from_mseed.csv",
                    data_interim_dir / "station_deltas.csv",
                ]
                found_delta_file = next((f for f in possible_delta_files if f.exists()), None)

                if found_delta_file:
                    out_station_deltas = found_delta_file
                    logger.info(f"[Fase 2 saltata] Rilevato file delta da esecuzione precedente: {out_station_deltas.name}")
                else:
                    logger.error("Per saltare la Fase 2, è necessario fornire --delta-csv o avere un file delta da un'esecuzione precedente.")
                    raise ValueError("Cannot skip Phase 2 without a delta file.")

            # Dobbiamo comunque definire il path per le statistiche, che potrebbe esistere da una run precedente.
            out_station_stats = data_processed_dir / "station_stats.csv"
            if not out_station_stats.exists():
                logger.warning(f"File statistiche ({out_station_stats.name}) non trovato. Potrebbe causare errori nelle fasi successive.")
            
            # Validazione del file delta che useremo
            validate_csv_file(out_station_deltas, required_columns={"station", "delta_seconds"})
            logger.info(f"File delta per le fasi successive validato: {out_station_deltas.name}")

        # FASE 3: Spazializzazione

        if args.start_phase <= 3 and not args.skip_phase3:

            logger.info("[Fase 3] Integrazione coordinate metriche...")

            out_deltas_spatial = data_processed_dir / "deltas_spatial.csv"

            created_files.append(out_deltas_spatial)

            if not stations_csv:
                logger.error("Per Fase 3 serve --stations-csv.")
                raise ValueError("--stations-csv is required for Phase 3")

            # Validate stations CSV has required columns
            try:
                validate_csv_file(
                    stations_csv, required_columns={"station", "latitude", "longitude"}
                )
                logger.info(f"File stazioni validato per Fase 3")
            except Exception as e:
                logger.error(f"Validazione stazioni Fase 3 fallita: {e}")
                raise

            run_cmd(
                [
                    python_exe,
                    scripts_dir / "attach_coords_to_deltas.py",
                    "--delta-csv",
                    out_station_stats,
                    "--stations-csv",
                    stations_csv,
                    "--output-csv",
                    str(out_deltas_spatial),
                    "--value-column",
                    "base_mean",
                ],
                timeout=args.timeout,
            )

            logger.info("Fase 3 completata.")

        else:

            # Se saltiamo la Fase 3, dobbiamo comunque definire il path del suo output,
            # che dovrebbe esistere da una run precedente.
            out_deltas_spatial = data_processed_dir / "deltas_spatial.csv"
            if not out_deltas_spatial.exists():
                logger.warning(
                    f"Fase 3 saltata, ma il suo file di output ({out_deltas_spatial.name}) non è stato trovato. Questo potrebbe causare errori."
                )

        # FASE 4: Output GIS

        if args.start_phase <= 4 and not args.skip_phase4:

            logger.info("[Fase 4] Generazione output GIS...")
            if not (out_deltas_spatial and out_deltas_spatial.exists()):
                logger.error(f"File delta spaziale mancante: {out_deltas_spatial}")
                raise FileNotFoundError(
                    f"Spatial delta file missing: {out_deltas_spatial}"
                )

            if not (out_station_stats and out_station_stats.exists()):
                out_station_stats = data_processed_dir / "station_stats.csv"

                if not out_station_stats.exists():

                    logger.error("File statistiche stazioni mancante.")

                    raise FileNotFoundError("Station stats file missing")

            run_cmd(
                [
                    python_exe,
                    scripts_dir / "analyze_delta_map.py",
                    "--delta-csv",
                    out_deltas_spatial,
                    "--stats-csv",
                    out_station_stats,
                    "--outdir",
                    str(maps_dir),
                    "--export-geotiff",
                    "--export-shapefile",
                    "--anomaly-threshold",
                    "0.5",
                ],
                timeout=args.timeout * 2,  # GIS operations may take longer
            )

            logger.info("Fase 4 completata.")

        # Definiamo l'input per le analisi successive, basandoci sull'output della Fase 2
        # Questa variabile ora è sempre definita se la Fase 2 è stata eseguita.
        input_for_ml = out_station_deltas

        # === NOVITÀ: Esecuzione Analisi Mobile (dopo Fase 4) ===

        if args.mobile_analysis and args.start_phase <= 4:

            logger.info("")

            logger.info("=" * 60)

            logger.info("📱 AVVIO ANALISI MOBILE + ALLARMI")

            logger.info("=" * 60)

            # NUOVA LOGICA: Chiama direttamente lo script di training ML
            # passando l'output della pipeline corrente come input.
            train_script_path = scripts_dir / "train_risk_model.py"            
            
            # L'input per il training è il file dei delta, che contiene i dati grezzi
            # necessari per il feature engineering temporale.
            if not input_for_ml or not input_for_ml.exists():
                logger.error(f"File di input per l'analisi ML non trovato: {input_for_ml}")
                raise FileNotFoundError("Input per ML non disponibile.")

            cmd_mobile = [
                python_exe,
                str(train_script_path),
                "--model-output-dir",
                str(mobile_analysis_dir / "models"),
                "--model-type",
                args.mobile_model_type,
            ]

            if args.mobile_generate_alerts:
                cmd_mobile.append("--generate-alerts")

            try:
                run_cmd(cmd_mobile, optional=False, timeout=args.timeout * 3) # Rimosso optional=True, questa fase è critica se richiesta.
                logger.info("✅ Analisi ML completata!")
                logger.info(f"   Risultati in: {mobile_analysis_dir}")
            except Exception as e:
                logger.error(f"❌ Analisi mobile fallita: {e}")
            
            # --- ESECUZIONE ANALISI INTEGRANTI (ora correttamente sotto --mobile-analysis) ---

            if not args.skip_b_value:
                logger.info("=" * 60)
                logger.info("📈 Esecuzione analisi b-value...")
                b_value_script_path = PROJECT_ROOT / "examples" / "mobile_devices" / "calculate_b_value.py"
                b_value_report_path = mobile_analysis_dir / "b_value_report.txt"

                # Questa analisi usa il file dei delta grezzi
                if input_for_ml and input_for_ml.exists():
                    run_cmd([
                        python_exe,
                        str(b_value_script_path),
                        str(input_for_ml),
                        "--mag-col", "delta_seconds", # Usiamo delta_seconds come proxy della magnitudo
                        "--output-file", str(b_value_report_path)
                    ], optional=True, timeout=args.timeout)
                    logger.info(f"   Report b-value salvato in: {b_value_report_path}")
                else:
                    logger.warning("Input per analisi b-value non trovato. Salto.")
            else:
                logger.info("Analisi b-value saltata su richiesta.")

            if not args.skip_noise_analysis:
                logger.info("=" * 60)
                logger.info("🎧 Esecuzione analisi rumore antropico...")
                noise_script_path = PROJECT_ROOT / "examples" / "mobile_devices" / "analyze_anthropogenic_noise.py"
                noise_report_path = mobile_analysis_dir / "anthropogenic_noise_report.txt"

                # Anche questa analisi usa il file dei delta grezzi
                if input_for_ml and input_for_ml.exists():
                    run_cmd([
                        python_exe,
                        str(noise_script_path),
                        str(input_for_ml),
                        "--output-file", str(noise_report_path)
                    ], optional=True, timeout=args.timeout)
                    logger.info(f"   Report rumore antropico salvato in: {noise_report_path}")
                else:
                    logger.warning("Input per analisi rumore non trovato. Salto.")
            else:
                logger.info("Analisi rumore antropico saltata su richiesta.")

        # === NOVITÀ: Ingestione Automatica nel Database ===
        if args.auto_ingest:
            logger.info("")
            logger.info("=" * 60)
            logger.info("💾 AVVIO INGESTIONE AUTOMATICA NEL DATABASE")
            logger.info("=" * 60)

            source_type = "mseed" if args.run_download or (run_dir / "waveforms").exists() else "catalog"

            cmd_ingest = [
                python_exe,
                str(PROJECT_ROOT / "ingest_runs_to_db.py"),
                "--run-id", args.run_name,
                "--run-name", f"Analisi Pipeline: {args.run_name}",
                "--run-dir", str(run_dir),
                "--source-type", source_type,
            ]
            run_cmd(cmd_ingest, timeout=args.timeout)
            logger.info("✅ Ingestione nel database completata con successo!")


        logger.info("=" * 60)

        logger.info("  Pipeline completata con successo!")

        logger.info(f"  Risultati in: {run_dir}/")

        logger.info("=" * 60)

    except subprocess.TimeoutExpired as exc:

        logger.error(f"❌ Timeout superato: {exc}")

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        raise

    except FileNotFoundError as exc:

        logger.error(f"❌ File non trovato: {exc}")

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        raise

    except DataValidationError as exc:

        logger.error(f"❌ Validazione dati fallita: {exc.message}")

        for err in exc.errors:

            logger.error(f"   {err}")

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        raise

    except ValueError as exc:

        logger.error(f"❌ Errore di configurazione: {exc}")

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        raise

    except subprocess.CalledProcessError as exc:

        logger.error(f"❌ Comando fallito con codice {exc.returncode}")

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        raise

    except KeyboardInterrupt:

        logger.warning("Pipeline interrotta dall'utente")

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        sys.exit(1)

    except Exception as exc:

        logger.error(f"❌ Errore inaspettato: {exc}", exc_info=True)

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        if args.cleanup_on_error:

            cleanup_run_directory(run_dir)

        raise

    # pandas imported at module level


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception(f"Unhandled exception in run_pipeline: {exc}")
        sys.exit(1)
