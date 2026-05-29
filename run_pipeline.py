#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Aggiungiamo la cartella scripts al path di sistema per importare utils
sys.path.append(str(PROJECT_ROOT / "scripts"))
from utils import setup_logger

logger = setup_logger("orchestrator")


def run_cmd(cmd_list):
    """
    Esegue un comando invocando un nuovo processo.
    Lancia un'eccezione se il comando fallisce (replica l'effetto di 'set -e' in Bash).
    """
    # Converte tutti gli argomenti in stringhe per sicurezza
    cmd_str = [str(arg) for arg in cmd_list]
    subprocess.run(cmd_str, check=True)


def main():
    parser = argparse.ArgumentParser(description="Orchestratore della Pipeline Sismologica Geospaziale")
    parser.add_argument(
        "--run-name", 
        type=str, 
        default=datetime.now().strftime("%Y%m%d_%H%M%S"), 
        help="Nome della cartella di esecuzione (default: timestamp corrente)."
    )
    args = parser.parse_args()

    logger.info("=======================================================")
    logger.info("  Pipeline Analisi Dati Sismici Generale")
    logger.info(f"  Esecuzione ID: {args.run_name}")
    logger.info("=======================================================")

    # Definizione delle directory base
    scripts_dir = PROJECT_ROOT / "scripts"
    data_raw_dir = PROJECT_ROOT / "data" / "raw"
    
    # Definizione della directory specifica per questa esecuzione (Run)
    run_dir = PROJECT_ROOT / "runs" / args.run_name
    data_interim_dir = run_dir / "interim"
    data_processed_dir = run_dir / "processed"
    maps_dir = run_dir / "maps"

    # Pulizia automatica per evitare sovrascritture parziali
    if run_dir.exists():
        logger.info(f"Pulizia della cartella di run esistente: {run_dir}")
        shutil.rmtree(run_dir)

    # Definizione dei file di input (Dati grezzi comuni)
    events_csv = data_raw_dir / "science.adw9038_data_s1.csv"
    picks_csv = data_raw_dir / "science.adw9038_data_s2.csv"
    stations_csv = data_raw_dir / "stations.csv"
    
    global_selected_stations = data_raw_dir / "selected_stations.txt"
    # File esportato dalla Fase 0 spaziale, ora isolato per questa esecuzione
    selected_stations_txt = run_dir / "selected_stations.txt"

    # Creazione delle cartelle di output
    data_interim_dir.mkdir(parents=True, exist_ok=True)
    data_processed_dir.mkdir(parents=True, exist_ok=True)
    maps_dir.mkdir(parents=True, exist_ok=True)

    # Se l'utente ha generato il file di selezione manualmente (Fase 0 da CLI indipendente), lo copiamo nella run
    if global_selected_stations.exists() and not selected_stations_txt.exists():
        logger.info(f"Importato filtro stazioni spaziale precedentemente generato.")
        shutil.copy(global_selected_stations, selected_stations_txt)

    # Ottiene il percorso esatto dell'eseguibile Python corrente
    python_exe = sys.executable

    try:
        # ======================================================================
        # FASE 0: Selezione Spaziale Stazioni (Opzionale)
        # ======================================================================
        # logger.info("[Fase 0] Selezione stazioni entro 20 km dal cratere/punto focale...")
        # run_cmd([
        #     python_exe, scripts_dir / "select_stations_spatial.py",
        #     "--input-csv", stations_csv,
        #     "--output-file", selected_stations_txt,
        #     "--point", "40.82", "14.14", "20.0"  # Sostituisci con LAT LON e Raggio (km)
        # ])

        # ======================================================================
        # FASE 1: Acquisizione Dati (Commentata di default)
        # ======================================================================
        # logger.info("[Fase 1] Download forme d'onda MiniSEED...")
        # run_cmd([
        #     python_exe, scripts_dir / "download_cf_waveforms.py", 
        #     "--stations-file", selected_stations_txt, # Utilizza le stazioni appena filtrate
        #     "--dry-run"
        # ])

        # ======================================================================
        # FASE 2 (Avanzata/Alternativa): Calcolo delta da forme d'onda raw
        # ======================================================================
        # logger.info("[Fase 2 - RAW] Estrazione delta direttamente dai MiniSEED...")
        # run_cmd([
        #     python_exe, scripts_dir / "compute_mseed_deltas.py",
        #     "--output-csv", data_interim_dir / "mseed_deltas.csv"
        # ])

        # ======================================================================
        # FASE 2: Elaborazione Tempi di Arrivo (Delta) e Statistiche
        # ======================================================================
        logger.info("[Fase 2 - Step 1] Preparazione dei delta dai cataloghi eventi e pick...")
        out_station_deltas = data_interim_dir / "station_deltas.csv"
        
        cmd_step1 = [
            python_exe, scripts_dir / "prepare_science_deltas.py",
            "--events-csv", events_csv,
            "--picks-csv", picks_csv,
            "--output-csv", out_station_deltas
        ]
        if selected_stations_txt.exists():
            logger.info(f"  -> Applico filtro spaziale stazioni da: {selected_stations_txt.name}")
            cmd_step1.extend(["--stations-file", selected_stations_txt])
        run_cmd(cmd_step1)

        logger.info("[Fase 2 - Step 2] Aggregazione e calcolo statistiche per stazione...")
        out_station_stats = data_processed_dir / "station_stats.csv"
        run_cmd([
            python_exe, scripts_dir / "compute_station_stats.py",
            "--base-csv", out_station_deltas,
            "--output-csv", out_station_stats
        ])

        # ======================================================================
        # FASE 3: Spazializzazione
        # ======================================================================
        # logger.info("[Fase 3 - Avanzata] Inversione per ricalcolare le coordinate ottimali delle stazioni...")
        # out_inverted_stations = data_processed_dir / "station_locations_inverted.csv"
        # run_cmd([
        #     python_exe, scripts_dir / "invert_station_locations.py",
        #     "--events-csv", events_csv,
        #     "--picks-csv", picks_csv,
        #     "--output-csv", out_inverted_stations
        # ])

        logger.info("[Fase 3] Integrazione delle coordinate metriche (EPSG:32633)...")
        out_deltas_spatial = data_processed_dir / "deltas_spatial.csv"
        run_cmd([
            python_exe, scripts_dir / "attach_coords_to_deltas.py",
            "--delta-csv", out_station_stats,
            "--stations-csv", stations_csv,
            "--output-csv", out_deltas_spatial,
            "--value-column", "base_mean"
        ])

        # ======================================================================
        # FASE 4: Output GIS e Grafici
        # ======================================================================
        logger.info("[Fase 4] Generazione output GIS (GeoTIFF, Shapefile) e plot anomalie...")
        run_cmd([
            python_exe, scripts_dir / "analyze_delta_map.py",
            "--delta-csv", out_deltas_spatial,
            "--stats-csv", out_station_stats,
            "--outdir", maps_dir,
            "--export-geotiff",
            "--export-shapefile",
            "--anomaly-threshold", "0.5"
        ])

        logger.info("=======================================================")
        logger.info("  Pipeline completata con successo! ")
        logger.info(f"  Dati e mappe sono stati salvati in: {run_dir}/")
        logger.info("=======================================================")

    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Errore critico: Lo step ha fallito con codice di uscita {e.returncode}.")
        sys.exit(1)

if __name__ == "__main__":
    main()
