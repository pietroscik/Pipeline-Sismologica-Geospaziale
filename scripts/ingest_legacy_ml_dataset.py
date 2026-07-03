#!/usr/bin/env python3
"""
Script per l'ingestione una-tantum di un dataset di feature ML storico.

Questo script legge un file CSV contenente feature di Machine Learning già elaborate
e le inserisce nella tabella `ml_features_timeseries` del database DuckDB.
Questo permette di unire dati storici con quelli nuovi generati dalla pipeline.

Usage:
    # Ingerisce il dataset di esempio del 2026
    python scripts/ingest_legacy_ml_dataset.py --input-csv "examples/mobile_devices/dataset_ml_sismico.csv"
"""

import argparse
import duckdb
import pandas as pd
from pathlib import Path
import sys

# Aggiungiamo la root del progetto al path per risolvere le dipendenze
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils import setup_logger
from ingest_runs_to_db import initialize_db_schema, DUCKDB_PATH

logger = setup_logger("ingest_legacy_ml")


def main():
    parser = argparse.ArgumentParser(description="Ingerisce un dataset ML storico in DuckDB.")
    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help="Percorso al file CSV del dataset ML storico.",
    )
    args = parser.parse_args()

    if not args.input_csv.exists():
        logger.error(f"File non trovato: {args.input_csv}")
        sys.exit(1)

    logger.info(f"🚀 Inizio ingestione del dataset storico: {args.input_csv.name}")

    # Assicuriamoci che la directory del database esista prima di connetterci.
    # DuckDB può creare il file, ma non le directory parent.
    db_dir = DUCKDB_PATH.parent
    if not db_dir.exists():
        logger.info(f"La directory del database non esiste. La creo in: {db_dir}")
        db_dir.mkdir(parents=True, exist_ok=True)

    try:
        con = duckdb.connect(database=str(DUCKDB_PATH), read_only=False)

        # Assicurati che lo schema esista
        initialize_db_schema(con)

        logger.info("Caricamento e pulizia del file CSV...")
        df = pd.read_csv(args.input_csv)

        # Rinomina la colonna del tempo per coerenza con lo schema DB
        if "Tempo" in df.columns:
            df.rename(columns={"Tempo": "timestamp"}, inplace=True)

        # Converte in formato datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Ottieni le colonne della tabella di destinazione per un inserimento sicuro
        target_table_info = con.execute("PRAGMA table_info('ml_features_timeseries');").fetchdf()
        target_columns = target_table_info['name'].tolist()

        # Seleziona solo le colonne del DataFrame che esistono nella tabella di destinazione
        columns_to_insert = [col for col in target_columns if col in df.columns]
        df_insert = df[columns_to_insert]

        logger.info(f"Trovate {len(columns_to_insert)} colonne corrispondenti per l'inserimento.")
        logger.info(f"Inserimento di {len(df_insert)} record nella tabella 'ml_features_timeseries'...")

        # Crea una stringa di colonne per un inserimento esplicito e robusto
        cols_sql_str = ", ".join(f'"{c}"' for c in columns_to_insert)

        # Inserisci i dati, ignorando i conflitti (se lo script viene eseguito più volte)
        con.execute(
            f"INSERT INTO ml_features_timeseries ({cols_sql_str}) SELECT {cols_sql_str} FROM df_insert ON CONFLICT (timestamp) DO NOTHING;"
        )

        inserted_count = con.execute("SELECT count(*) FROM df_insert").fetchone()[0]
        logger.info(f"✅ Ingestione completata. {inserted_count} record processati.")

    except Exception as e:
        logger.error(f"❌ Errore durante l'ingestione: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if 'con' in locals() and con:
            con.close()


if __name__ == "__main__":
    main()