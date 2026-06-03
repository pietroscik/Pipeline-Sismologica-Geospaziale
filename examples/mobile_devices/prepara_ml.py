import pandas as pd
import numpy as np
import time
import logging
from pathlib import Path
from typing import Tuple, Optional

# Importa funzioni di validazione e calcolo
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "mobile"))
from data_validator import validate_data, haversine_distance, calculate_mean_distance, calculate_bearing, calculate_mean_direction

# Configura logging
logger = logging.getLogger(__name__)

# Costanti per Campi Flegrei
LAT_CENTRO = 40.8062
LON_CENTRO = 14.1410


def load_data(catalogo_path: str = "catalogo_terremoti_unici.csv") -> pd.DataFrame:
    """Carica e valida il catalogo eventi."""
    logger.info("📖 Caricamento del catalogo eventi...")
    df = pd.read_csv(catalogo_path)
    
    # Validazione
    is_valid, message, df_validated = validate_data(df, required_columns=['event_id', 'Tempo_Riferimento_ISO', 'Numero_Stazioni_Attivate'])
    if not is_valid:
        logger.error(f"❌ Errore validazione dati: {message}")
        raise ValueError(message)
    
    df['Tempo'] = pd.to_datetime(df['Tempo_Riferimento_ISO'])
    df.set_index('Tempo', inplace=True)
    df.sort_index(inplace=True)
    
    logger.info(f"✅ Caricati {len(df)} eventi unici")
    return df


def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """Aggiunge feature temporali avanzate."""
    logger.info("⚙️ Aggiunta feature temporali...")
    
    # 1. Resampling: Raggruppamento per ora
    df_orario = df.resample('1h').agg(
        numero_eventi=('event_id', 'count'),
        energia_max=('Numero_Stazioni_Attivate', 'max'),
        energia_media=('Numero_Stazioni_Attivate', 'mean'),
        energia_std=('Numero_Stazioni_Attivate', 'std'),
        energia_min=('Numero_Stazioni_Attivate', 'min')
    ).fillna(0)
    
    # 2. Rolling features (finestre temporali)
    for window in [6, 12, 24, 48]:
        df_orario[f'eventi_ultime_{window}h'] = df_orario['numero_eventi'].rolling(window=window).sum()
        df_orario[f'energia_max_ultime_{window}h'] = df_orario['energia_max'].rolling(window=window).max()
        df_orario[f'energia_media_ultime_{window}h'] = df_orario['energia_media'].rolling(window=window).mean()
        df_orario[f'energia_std_ultime_{window}h'] = df_orario['energia_std'].rolling(window=window).mean()
    
    # 3. Trend features (pendenza regressione lineare)
    for window in [12, 24]:
        df_orario[f'trend_eventi_{window}h'] = df_orario['numero_eventi'].rolling(window=window).apply(
            lambda x: np.polyfit(np.arange(len(x)), x, 1)[0] if len(x) > 1 else 0, raw=False
        )
        df_orario[f'trend_energia_{window}h'] = df_orario['energia_max'].rolling(window=window).apply(
            lambda x: np.polyfit(np.arange(len(x)), x, 1)[0] if len(x) > 1 else 0, raw=False
        )
    
    # 4. Varianza
    df_orario['varianza_eventi_24h'] = df_orario['numero_eventi'].rolling(24).var()
    df_orario['varianza_energia_24h'] = df_orario['energia_max'].rolling(24).var()
    
    # 5. Feature temporali aggiuntive
    df_orario['ora_del_giorno'] = df_orario.index.hour
    df_orario['giorno_della_settimana'] = df_orario.index.dayofweek
    df_orario['is_notte'] = df_orario.index.hour.isin([0,1,2,3,4,5,22,23]).astype(int)
    df_orario['is_weekend'] = df_orario.index.dayofweek.isin([5,6]).astype(int)
    
    # 6. Rate di cambiamento
    df_orario['event_rate_1h'] = df_orario['numero_eventi'].diff()
    df_orario['event_rate_6h'] = df_orario['numero_eventi'].diff(6)
    df_orario['energia_rate_1h'] = df_orario['energia_max'].diff()
    
    return df_orario


def add_future_target(df_orario: pd.DataFrame, target_window: int = 24, threshold: int = 18) -> pd.DataFrame:
    """Aggiunge la variabile target: evento con >=threshold stazioni nelle prossime N ore."""
    logger.info(f"🎯 Generazione target (finestra {target_window}h, soglia {threshold} stazioni)...")
    
    # Calcola il massimo numero di stazioni nelle prossime N ore
    df_orario['max_energia_futura'] = df_orario['energia_max'].rolling(window=target_window, min_periods=1).max().shift(-target_window)
    
    # Target: 1 se ci sara un evento con >=threshold stazioni nelle prossime N ore
    df_orario['Target_Allarme'] = (df_orario['max_energia_futura'] >= threshold).astype(int)
    
    # Conta quanti eventi futuri superano la soglia
    df_orario['future_events_above_threshold'] = df_orario['energia_max'].rolling(window=target_window, min_periods=1).apply(
        lambda x: (x >= threshold).sum()
    ).shift(-target_window).fillna(0)
    
    logger.info(f"📊 Target generato: {df_orario['Target_Allarme'].sum()} allarmi su {len(df_orario)} ore")
    return df_orario


def add_spatial_features(
    df_orario: pd.DataFrame,
    df_events: pd.DataFrame,
    stations: pd.DataFrame,
    center_lat: float = LAT_CENTRO,
    center_lon: float = LON_CENTRO
) -> pd.DataFrame:
    """Aggiunge feature spaziali al dataset orario."""
    logger.info("🗺️ Aggiunta feature spaziali...")
    
    # 1. Carica dati geospaziali
    try:
        df_geo = pd.read_csv("output_eventi_georeferenziati.csv.gz")
        df_geo['arrival_iso'] = pd.to_datetime(df_geo['arrival_iso'])
    except FileNotFoundError:
        logger.warning("File output_eventi_georeferenziati.csv.gz non trovato, feature spaziali saltate")
        return df_orario
    
    # 2. Calcola feature spaziali per ogni ora
    df_geo['ora'] = df_geo['arrival_iso'].dt.floor('1h')
    
    # Aggrega per ora
    spatial_by_hour = df_geo.groupby('ora').agg(
        num_stations=('station', 'nunique'),
        mean_latitude=('latitude', 'mean'),
        mean_longitude=('longitude', 'mean'),
        std_latitude=('latitude', 'std'),
        std_longitude=('longitude', 'std'),
        mean_delta=('delta_seconds', 'mean'),
        std_delta=('delta_seconds', 'std')
    )
    
    # Merge con df_orario
    df_orario = df_orario.join(spatial_by_hour, how='left').fillna(0)
    
    # 3. Calcola distanza e direzione dal centro
    df_orario['distanza_da_centro_km'] = df_orario.apply(
        lambda row: haversine_distance(LON_CENTRO, LAT_CENTRO, row['mean_longitude'], row['mean_latitude'])
        if pd.notna(row['mean_longitude']) else 0.0,
        axis=1
    )
    
    df_orario['direzione_da_centro_deg'] = df_orario.apply(
        lambda row: calculate_mean_direction(
            df_geo[df_geo['ora'] == row.name],
            LON_CENTRO, LAT_CENTRO
        ) if row.name in df_geo['ora'].values else 0.0,
        axis=1
    )
    
    # 4. Dispersione spaziale
    df_orario['dispersione_spaziale_km'] = df_orario['std_latitude'] * 111.32
    
    return df_orario


def add_seismological_features(df_orario: pd.DataFrame, bvalue_window: int = 24) -> pd.DataFrame:
    """Aggiunge feature sismologiche (b-value, ecc.)."""
    logger.info("🌋 Aggiunta feature sismologiche...")
    
    try:
        df_catalogo = pd.read_csv("catalogo_terremoti_unici.csv")
        df_catalogo['Tempo_Riferimento_ISO'] = pd.to_datetime(df_catalogo['Tempo_Riferimento_ISO'])
        df_catalogo['ora'] = df_catalogo['Tempo_Riferimento_ISO'].dt.floor('1h')
        
        bvalue_by_hour = df_catalogo.groupby('ora').agg(
            bvalue_approx=('Numero_Stazioni_Attivate', 'mean'),
            event_count=('event_id', 'count')
        )
        
        df_orario = df_orario.join(bvalue_by_hour, how='left').fillna(0)
        
        # Rolling b-value
        df_orario['bvalue_rolling_24h'] = df_orario['bvalue_approx'].rolling(window=bvalue_window).mean()
        df_orario['bvalue_std_24h'] = df_orario['bvalue_approx'].rolling(window=bvalue_window).std()
        df_orario['bvalue_change'] = df_orario['bvalue_approx'].diff()
        
    except FileNotFoundError:
        logger.warning("File catalogo_terremoti_unici.csv non trovato, feature sismologiche saltate")
    except Exception as e:
        logger.warning(f"Errore calcolo feature sismologiche: {e}")
    
    return df_orario


def add_statistical_features(df_orario: pd.DataFrame) -> pd.DataFrame:
    """Aggiunge feature statistiche avanzate."""
    logger.info("📊 Aggiunta feature statistiche...")
    
    # 1. Percentili
    for percentile in [25, 50, 75, 90, 95]:
        df_orario[f'energia_p{percentile}_24h'] = df_orario['energia_max'].rolling(24).quantile(percentile/100)
    
    # 2. Skewness e Kurtosis
    df_orario['skewness_eventi_24h'] = df_orario['numero_eventi'].rolling(24).skew()
    df_orario['kurtosis_eventi_24h'] = df_orario['numero_eventi'].rolling(24).kurtosis()
    
    # 3. Entropia
    df_orario['entropy_24h'] = df_orario['numero_eventi'].rolling(24).apply(
        lambda x: -np.sum((x/x.sum()) * np.log2(x/x.sum() + 1e-10)) if x.sum() > 0 else 0, raw=False
    )
    
    # 4. Autocorrelazione
    df_orario['autocorr_1h'] = df_orario['numero_eventi'].autocorr(1)
    df_orario['autocorr_6h'] = df_orario['numero_eventi'].autocorr(6)
    
    return df_orario


def remove_missing_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rimuove righe con troppi valori mancanti."""
    nan_counts = df.isnull().sum(axis=1)
    threshold = len(df.columns) * 0.5
    df_clean = df[nan_counts <= threshold].copy()
    
    removed = len(df) - len(df_clean)
    if removed > 0:
        logger.info(f"🧹 Rimosse {removed} righe con troppi valori mancanti")
    
    return df_clean


def main():
    """Esecuzione principale del feature engineering."""
    print("🚀 Avvio Feature Engineering Avanzato per Machine Learning...")
    tempo_inizio = time.time()
    
    try:
        # Setup logging
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "mobile"))
        from logging_config import setup_logging
        setup_logging()
        
        # 1. Caricamento dati
        df = load_data()
        
        # 2. Aggiunta feature temporali
        df_orario = add_temporal_features(df)
        
        # 3. Aggiunta target futuro
        df_orario = add_future_target(df_orario, target_window=24, threshold=18)
        
        # 4. Aggiunta feature spaziali
        try:
            stations = pd.read_csv("stations.csv")
            df_orario = add_spatial_features(df_orario, df, stations)
        except FileNotFoundError:
            logger.warning("File stations.csv non trovato, feature spaziali saltate")
        except Exception as e:
            logger.warning(f"Errore feature spaziali: {e}")
        
        # 5. Aggiunta feature sismologiche
        df_orario = add_seismological_features(df_orario)
        
        # 6. Aggiunta feature statistiche
        df_orario = add_statistical_features(df_orario)
        
        # 7. Rimuovi righe con NaN
        df_ml = remove_missing_rows(df_orario)
        
        # 8. Salvataggio
        FILE_OUT = "dataset_ml_sismico.csv"
        df_ml.to_csv(FILE_OUT, index=True, index_label='Tempo')
        
        tempo_elaborazione = time.time() - tempo_inizio
        print(f"
📊 === SINTESI DATASET MACHINE LEARNING ===")
        print("-" * 60)
        print(f"Righe totali (Ore campionate): {len(df_ml)}")
        print(f"Ore con Allarme (Target=1): {df_ml['Target_Allarme'].sum()}")
        print(f"Percentuale allarmi: {df_ml['Target_Allarme'].sum() / len(df_ml) * 100:.2f}%")
        print(f"Feature totali: {len(df_ml.columns) - 1}")
        print("-" * 60)
        print(f"
✅ Dataset addestramento avanzato salvato: '{FILE_OUT}'")
        print(f"⏱️ Tempo impiegato: {tempo_elaborazione:.2f}s")
        
        # Mostra prime righe
        print("
📋 Anteprima dataset:")
        print(df_ml.head().T)
        
    except Exception as e:
        logger.error(f"❌ Errore critico in prepara_ml.py: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()