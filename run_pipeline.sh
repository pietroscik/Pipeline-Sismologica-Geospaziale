#!/usr/bin/env bash

set -e

RUN_NAME=$(date +"%Y%m%d_%H%M%S")
START_PHASE=2
SKIP_PHASE0=false
SKIP_PHASE1=false
SKIP_PHASE2=false
SKIP_PHASE3=false
SKIP_PHASE4=false
EVENTS_CSV=""
PICKS_CSV=""
STATIONS_CSV=""
DELTA_CSV=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --run-name)
            RUN_NAME="$2"
            shift ;;
        --start-phase)
            START_PHASE="$2"
            shift ;;
        --skip-phase0) SKIP_PHASE0=true ;;
        --skip-phase1) SKIP_PHASE1=true ;;
        --skip-phase2) SKIP_PHASE2=true ;;
        --skip-phase3) SKIP_PHASE3=true ;;
        --skip-phase4) SKIP_PHASE4=true ;;
        --events-csv)
            EVENTS_CSV="$2"
            shift ;;
        --picks-csv)
            PICKS_CSV="$2"
            shift ;;
        --stations-csv)
            STATIONS_CSV="$2"
            shift ;;
        --delta-csv)
            DELTA_CSV="$2"
            shift ;;
        *)
            echo "Parametro sconosciuto: $1"
            echo "Uso: $0 [--run-name NOME] [--start-phase N] [--skip-phaseX] [--events-csv FILE] [--picks-csv FILE] [--stations-csv FILE] [--delta-csv FILE]"
            exit 1 ;;
    esac
done

echo "======================================================="
echo "  Pipeline Analisi Dati Sismici Geospaziale"
echo "  Esecuzione ID: $RUN_NAME"
echo "  Fase di partenza: $START_PHASE"
echo "======================================================="

PROJECT_ROOT=$(pwd)
SCRIPTS_DIR="$PROJECT_ROOT/scripts"
DATA_RAW="$PROJECT_ROOT/data/raw"

RUN_DIR="$PROJECT_ROOT/runs/$RUN_NAME"
DATA_INTERIM="$RUN_DIR/interim"
DATA_PROCESSED="$RUN_DIR/processed"
MAPS_DIR="$RUN_DIR/maps"

if [ -d "$RUN_DIR" ]; then
    echo "Pulizia parziale della cartella di run esistente: $RUN_DIR"
    rm -rf "$DATA_INTERIM" "$DATA_PROCESSED" "$MAPS_DIR"
fi

GLOBAL_SELECTED="$DATA_RAW/selected_stations.txt"
SELECTED_STATIONS_TXT="$RUN_DIR/selected_stations.txt"

mkdir -p "$DATA_INTERIM"
mkdir -p "$DATA_PROCESSED"
mkdir -p "$MAPS_DIR"

if [ -f "$GLOBAL_SELECTED" ] && [ ! -f "$SELECTED_STATIONS_TXT" ]; then
    echo "Importato filtro stazioni spaziale precedentemente generato."
    cp "$GLOBAL_SELECTED" "$SELECTED_STATIONS_TXT"
fi

file_exists() {
    [ -f "$1" ]
}

OUT_STATION_DELTAS=""
OUT_STATION_STATS=""
OUT_DELTAS_SPATIAL=""

# FASE 0
if [ "$START_PHASE" -le 0 ] && [ "$SKIP_PHASE0" = false ]; then
    if [ -n "$STATIONS_CSV" ] && file_exists "$STATIONS_CSV"; then
        echo ""
        echo "[Fase 0] Selezione stazioni spaziale..."
        python "$SCRIPTS_DIR/select_stations_spatial.py" --input-csv "$STATIONS_CSV" --output-file "$SELECTED_STATIONS_TXT" --point 40.82 14.14 20.0
        echo "Fase 0 completata."
    else
        echo "Attenzione: --stations-csv non specificato. Fase 0 saltata."
    fi
fi

# FASE 1
if [ "$START_PHASE" -le 1 ] && [ "$SKIP_PHASE1" = false ]; then
    if file_exists "$SELECTED_STATIONS_TXT"; then
        echo ""
        echo "[Fase 1] Download forme d'onda..."
        python "$SCRIPTS_DIR/download_cf_waveforms.py" --stations-file "$SELECTED_STATIONS_TXT" --dry-run
        echo "Fase 1 completata (dry-run)."
    else
        echo "Attenzione: Nessun file stazioni selezionate. Fase 1 saltata."
    fi
fi

# FASE 2
if [ "$START_PHASE" -le 2 ] && [ "$SKIP_PHASE2" = false ]; then
    if [ -n "$DELTA_CSV" ] && file_exists "$DELTA_CSV"; then
        echo ""
        echo "[Fase 2] Utilizzo file delta pre-esistente: $DELTA_CSV"
        OUT_STATION_DELTAS="$DELTA_CSV"
        OUT_STATION_STATS="$DATA_PROCESSED/station_stats.csv"
        echo ""
        echo "[Fase 2] Calcolo statistiche..."
        CMD="python $SCRIPTS_DIR/compute_station_stats.py --base-csv $OUT_STATION_DELTAS --output-csv $OUT_STATION_STATS"
        if [ -f "$SELECTED_STATIONS_TXT" ]; then
            CMD="$CMD --stations-file $SELECTED_STATIONS_TXT"
        fi
        eval "$CMD"
    else
        if file_exists "$EVENTS_CSV" && file_exists "$PICKS_CSV"; then
            OUT_STATION_DELTAS="$DATA_INTERIM/station_deltas.csv"
            echo ""
            echo "[Fase 2 - Step 1] Preparazione delta..."
            CMD="python $SCRIPTS_DIR/prepare_science_deltas.py --events-csv $EVENTS_CSV --picks-csv $PICKS_CSV --output-csv $OUT_STATION_DELTAS"
            if [ -f "$SELECTED_STATIONS_TXT" ]; then
                CMD="$CMD --stations-file $SELECTED_STATIONS_TXT"
            fi
            eval "$CMD"
            OUT_STATION_STATS="$DATA_PROCESSED/station_stats.csv"
            echo ""
            echo "[Fase 2 - Step 2] Aggregazione statistiche..."
            CMD="python $SCRIPTS_DIR/compute_station_stats.py --base-csv $OUT_STATION_DELTAS --output-csv $OUT_STATION_STATS"
            if [ -f "$SELECTED_STATIONS_TXT" ]; then
                CMD="$CMD --stations-file $SELECTED_STATIONS_TXT"
            fi
            eval "$CMD"
            echo "Fase 2 completata."
        else
            echo "Per Fase 2 servono --events-csv e --picks-csv o --delta-csv."
            exit 1
        fi
    fi
else
    if [ -n "$DELTA_CSV" ] && file_exists "$DELTA_CSV"; then
        OUT_STATION_DELTAS="$DELTA_CSV"
        OUT_STATION_STATS="$DATA_PROCESSED/station_stats.csv"
        echo "[Saltata Fase 2] Utilizzo delta: $DELTA_CSV"
    else
        echo "Per saltare Fase 2, serve --delta-csv."
        exit 1
    fi
fi

# FASE 3
if [ "$START_PHASE" -le 3 ] && [ "$SKIP_PHASE3" = false ]; then
    if [ -n "$STATIONS_CSV" ] && file_exists "$STATIONS_CSV"; then
        OUT_DELTAS_SPATIAL="$DATA_PROCESSED/deltas_spatial.csv"
        echo ""
        echo "[Fase 3] Integrazione coordinate..."
        python "$SCRIPTS_DIR/attach_coords_to_deltas.py" --delta-csv "$OUT_STATION_STATS" --stations-csv "$STATIONS_CSV" --output-csv "$OUT_DELTAS_SPATIAL" --value-column base_mean
        echo "Fase 3 completata."
    else
        echo "Per Fase 3 serve --stations-csv."
        exit 1
    fi
else
    if [ -n "$DELTA_CSV" ]; then
        OUT_DELTAS_SPATIAL="$DATA_PROCESSED/deltas_spatial.csv"
        if [ ! -f "$OUT_DELTAS_SPATIAL" ]; then
            echo "Attenzione: Fase 3 saltata. Assicurati che $OUT_DELTAS_SPATIAL esista."
        fi
    fi
fi

# FASE 4
if [ "$START_PHASE" -le 4 ] && [ "$SKIP_PHASE4" = false ]; then
    if [ -n "$OUT_DELTAS_SPATIAL" ] && file_exists "$OUT_DELTAS_SPATIAL"; then
        if [ -n "$OUT_STATION_STATS" ] && file_exists "$OUT_STATION_STATS"; then
            echo ""
            echo "[Fase 4] Generazione output GIS..."
            python "$SCRIPTS_DIR/analyze_delta_map.py" --delta-csv "$OUT_DELTAS_SPATIAL" --stats-csv "$OUT_STATION_STATS" --outdir "$MAPS_DIR" --export-geotiff --export-shapefile --anomaly-threshold 0.5
            echo "Fase 4 completata."
        else
            echo "File statistiche mancante."
            exit 1
        fi
    else
        echo "File delta spaziale mancante: $OUT_DELTAS_SPATIAL"
        exit 1
    fi
fi

echo ""
echo "======================================================="
echo "  Pipeline completata con successo!"
echo "  Risultati in: $RUN_DIR/"
echo "======================================================="
