#!/usr/bin/env bash

# Interrompe l'esecuzione immediatamente in caso di errore di un singolo step
set -e

# Parsing argomenti per isolare le esecuzioni
RUN_NAME=$(date +"%Y%m%d_%H%M%S")
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --run-name) RUN_NAME="$2"; shift ;;
        *) echo "Parametro sconosciuto: $1"; exit 1 ;;
    esac
    shift
done

echo "======================================================="
echo "  Pipeline Analisi Dati Sismici Generale"
echo "  Esecuzione ID: $RUN_NAME"
echo "======================================================="

# Definizione delle directory base collegate al percorso dello script
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"
DATA_RAW="$PROJECT_ROOT/data/raw"

# Definizione della directory specifica per questa esecuzione (Run)
RUN_DIR="$PROJECT_ROOT/runs/$RUN_NAME"
DATA_INTERIM="$RUN_DIR/interim"
DATA_PROCESSED="$RUN_DIR/processed"
MAPS_DIR="$RUN_DIR/maps"

# Pulizia automatica per evitare sovrascritture parziali
if [ -d "$RUN_DIR" ]; then
    echo "Pulizia parziale della cartella di run esistente: $RUN_DIR"
    rm -rf "$DATA_INTERIM" "$DATA_PROCESSED" "$MAPS_DIR"
fi

# Definizione dei file di input (modifica questi percorsi se necessario)
EVENTS_CSV="$DATA_RAW/science.adw9038_data_s1.csv"
PICKS_CSV="$DATA_RAW/science.adw9038_data_s2.csv"
STATIONS_CSV="$DATA_RAW/stations.csv"
GLOBAL_SELECTED="$DATA_RAW/selected_stations.txt"
SELECTED_STATIONS_TXT="$RUN_DIR/selected_stations.txt"

# Creazione delle cartelle di output
mkdir -p "$DATA_INTERIM"
mkdir -p "$DATA_PROCESSED"
mkdir -p "$MAPS_DIR"

# Se l'utente ha generato il file di selezione manualmente (Fase 0 da CLI indipendente), lo copiamo nella run
if [ -f "$GLOBAL_SELECTED" ] && [ ! -f "$SELECTED_STATIONS_TXT" ]; then
    cp "$GLOBAL_SELECTED" "$SELECTED_STATIONS_TXT"
fi

# ======================================================================
# FASE 0: Selezione Spaziale Stazioni (Opzionale)
# ======================================================================
# echo "[Fase 0] Selezione stazioni entro 20 km dal punto focale..."
# python "$SCRIPTS_DIR/select_stations_spatial.py" \
#   --input-csv "$STATIONS_CSV" \
#   --output-file "$SELECTED_STATIONS_TXT" \
#   --point 40.82 14.14 20.0

# ======================================================================
# FASE 1: Acquisizione Dati (Commentata per default per evitare lunghi tempi di attesa)
# ======================================================================
# echo "[Fase 1] Download forme d'onda MiniSEED..."
# python "$SCRIPTS_DIR/download_cf_waveforms.py" \
#   --stations-file "$SELECTED_STATIONS_TXT" \
#   --dry-run

# ======================================================================
# FASE 2 (Avanzata/Alternativa): Calcolo delta da forme d'onda raw
# ======================================================================
# echo "[Fase 2 - RAW] Estrazione delta direttamente dai MiniSEED..."
# python "$SCRIPTS_DIR/compute_mseed_deltas.py" \
#   --output-csv "$DATA_INTERIM/mseed_deltas.csv"

# ======================================================================
# FASE 2: Elaborazione Tempi di Arrivo (Delta) e Statistiche
# ======================================================================
echo ""
echo "[Fase 2 - Step 1] Preparazione dei delta dai cataloghi eventi e pick..."

STEP1_CMD=(python "$SCRIPTS_DIR/prepare_science_deltas.py" \
  --events-csv "$EVENTS_CSV" \
  --picks-csv "$PICKS_CSV" \
  --output-csv "$DATA_INTERIM/station_deltas.csv")

if [ -f "$SELECTED_STATIONS_TXT" ]; then
    echo "  -> Applico filtro spaziale stazioni"
    STEP1_CMD+=(--stations-file "$SELECTED_STATIONS_TXT")
fi

"${STEP1_CMD[@]}"

echo ""
echo "[Fase 2 - Step 2] Aggregazione e calcolo statistiche per stazione..."
python "$SCRIPTS_DIR/compute_station_stats.py" \
  --base-csv "$DATA_INTERIM/station_deltas.csv" \
  --output-csv "$DATA_PROCESSED/station_stats.csv"

# ======================================================================
# FASE 3: Spazializzazione
# ======================================================================
echo ""
# echo "[Fase 3 - Avanzata] Inversione per ricalcolare le coordinate ottimali delle stazioni..."
# python "$SCRIPTS_DIR/invert_station_locations.py" \
#   --events-csv "$EVENTS_CSV" \
#   --picks-csv "$PICKS_CSV" \
#   --output-csv "$DATA_PROCESSED/station_locations_inverted.csv"

echo ""
echo "[Fase 3] Integrazione delle coordinate metriche (EPSG:32633)..."
python "$SCRIPTS_DIR/attach_coords_to_deltas.py" \
  --delta-csv "$DATA_PROCESSED/station_stats.csv" \
  --stations-csv "$STATIONS_CSV" \
  --output-csv "$DATA_PROCESSED/deltas_spatial.csv" \
  --value-column "base_mean"

# ======================================================================
# FASE 4: Output GIS e Grafici
# ======================================================================
echo ""
echo "[Fase 4] Generazione output GIS (GeoTIFF, Shapefile) e plot anomalie..."
python "$SCRIPTS_DIR/analyze_delta_map.py" \
  --delta-csv "$DATA_PROCESSED/deltas_spatial.csv" \
  --stats-csv "$DATA_PROCESSED/station_stats.csv" \
  --outdir "$MAPS_DIR" \
  --export-geotiff \
  --export-shapefile

echo ""
echo "======================================================="
echo "  Pipeline completata con successo! "
echo "  Dati e mappe sono stati salvati in: $RUN_DIR/"
echo "======================================================="