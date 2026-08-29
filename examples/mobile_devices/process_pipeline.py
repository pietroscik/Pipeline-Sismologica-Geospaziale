#!/usr/bin/env python3
"""
Pipeline di elaborazione mobile per dati sismici.

Questa pipeline elabora dati sismici provenienti da dispositivi mobili,
effettua la pulizia, preprocessing e preparazione per l'analisi ML.
"""

import argparse
import logging
import os
import pandas as pd
from pathlib import Path
import numpy as np
from datetime import datetime

# Configurazione logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_and_validate_data(input_csv: Path, stations_csv: Path):
    """Carica e valida i dati di input."""
    logger.info(f"Caricamento dati da {input_csv} e {stations_csv}")
    
    # Carica i dati sismici
    if not input_csv.exists():
        raise FileNotFoundError(f"File input non trovato: {input_csv}")
    
    if not stations_csv.exists():
        raise FileNotFoundError(f"File stazioni non trovato: {stations_csv}")
    
    df = pd.read_csv(input_csv)
    stations_df = pd.read_csv(stations_csv)
    
    logger.info(f"Dati caricati: {len(df)} righe, {len(stations_df)} stazioni")
    
    return df, stations_df


def clean_and_prepare_data(df, stations_df):
    """Effettua la pulizia e preparazione dei dati."""
    logger.info("Inizio pulizia e preparazione dati")
    
    # Unisci i dati con le informazioni delle stazioni
    if 'station' in df.columns and 'station' in stations_df.columns:
        df = df.merge(stations_df[['station', 'latitude', 'longitude']], 
                     on='station', how='left', suffixes=('', '_station'))
    
    # Rimuovi duplicati
    initial_count = len(df)
    df = df.drop_duplicates()
    logger.info(f"Rimossi {initial_count - len(df)} duplicati")
    
    # Gestisci valori nulli
    df = df.fillna(method='ffill').fillna(method='bfill')
    
    # Filtra dati anomali
    numeric_columns = df.select_dtypes(include=[np.number]).columns
    for col in numeric_columns:
        Q1 = df[col].quantile(0.25)
        Q3 = df[col].quantile(0.75)
        IQR = Q3 - Q1
        lower_bound = Q1 - 1.5 * IQR
        upper_bound = Q3 + 1.5 * IQR
        df = df[(df[col] >= lower_bound) & (df[col] <= upper_bound)]
    
    logger.info(f"Dati puliti: {len(df)} righe rimaste")
    
    return df


def extract_features(df):
    """Estrae caratteristiche utili per l'analisi ML."""
    logger.info("Estrazione caratteristiche")
    
    # Assicurati che ci siano colonne temporali
    timestamp_cols = [col for col in df.columns if 'time' in col.lower() or 'date' in col.lower()]
    if timestamp_cols:
        # Converti la prima colonna temporale trovata
        ts_col = timestamp_cols[0]
        df[ts_col] = pd.to_datetime(df[ts_col], errors='coerce')
    
    # Calcola alcune statistiche di base
    if 'amplitude' in df.columns:
        df['log_amplitude'] = np.log1p(np.abs(df['amplitude']))
        
    if 'frequency' in df.columns:
        df['freq_band'] = pd.cut(df['frequency'], bins=5, labels=['very_low', 'low', 'mid', 'high', 'very_high'])
    
    # Aggiungi indicatori di qualità
    df['data_quality_score'] = 1.0  # Placeholder - in realtà dovrebbe essere calcolato
    
    return df


def save_processed_data(df, output_dir: Path):
    """Salva i dati elaborati in formato pronto per la successiva fase."""
    logger.info(f"Salvataggio dati elaborati in {output_dir}")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Salva il dataset completo
    output_file = output_dir / "output_eventi_georeferenziati.csv.gz"
    df.to_csv(output_file, index=False, compression='gzip')
    logger.info(f"Dati elaborati salvati in {output_file}")
    
    # Salva anche un subset per analisi veloci
    subset_file = output_dir / "output_eventi_georeferenziati_subset.csv"
    df.head(min(1000, len(df))).to_csv(subset_file, index=False)
    logger.info(f"Subset dati salvato in {subset_file}")
    
    return output_file


def main():
    parser = argparse.ArgumentParser(description='Pipeline di elaborazione dati sismici mobile')
    parser.add_argument('--input-csv', type=Path, required=True, help='File CSV di input con dati sismici')
    parser.add_argument('--stations-csv', type=Path, required=True, help='File CSV con coordinate stazioni')
    parser.add_argument('--output-dir', type=Path, required=True, help='Directory di output per dati elaborati')
    parser.add_argument('--min-stations', type=int, default=18, help='Numero minimo di stazioni richieste')
    
    args = parser.parse_args()
    
    logger.info("Inizio elaborazione pipeline mobile")
    
    try:
        # Carica e valida i dati
        df, stations_df = load_and_validate_data(args.input_csv, args.stations_csv)
        
        # Pulisci e prepara i dati
        df_clean = clean_and_prepare_data(df, stations_df)
        
        # Estrai caratteristiche
        df_features = extract_features(df_clean)
        
        # Salva i dati elaborati
        output_file = save_processed_data(df_features, args.output_dir)
        
        logger.info("Elaborazione pipeline mobile completata con successo")
        
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione della pipeline: {str(e)}")
        raise


if __name__ == "__main__":
    main()