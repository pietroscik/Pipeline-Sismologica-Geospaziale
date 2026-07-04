import argparse
import duckdb
import pandas as pd
import pandera as pa
from pandera.typing import Series
from pathlib import Path
from datetime import datetime, timezone
import sys
import hashlib
from scripts.utils import get_project_root
from scripts.utils import setup_logger, load_config

logger = setup_logger("ingest_to_db")

# --- Data Validation Schemas with Pandera ---

class RawDeltasSchema(pa.DataFrameSchema):
    """Schema per validare i dati grezzi dei delta (es. station_deltas.csv)."""
    event_id: Series[str]
    network: Series[str]
    station: Series[str]
    channel: Series[str]
    arrival_epoch: Series[float]
    arrival_iso: Series[str]
    event_reference_epoch: Series[float]
    delta_seconds: Series[float]

    class Config:
        coerce = True
        strict = "filter"  # Ignora colonne extra non definite nello schema

class StationStatsSchema(pa.DataFrameSchema):
    """Schema per validare i dati delle statistiche per stazione."""
    station: Series[str]
    reference_date: Series[str]
    base_count: Series[int]
    base_mean: Series[float]
    base_std: Series[float]
    base_median: Series[float]
    soft_count: Series[int]
    soft_mean: Series[float]
    soft_std: Series[float]
    soft_median: Series[float]
    soft_minus_base_mean: Series[float]

    class Config:
        coerce = True
        strict = "filter"

class DeltasSpatialSchema(pa.DataFrameSchema):
    """Schema per validare i dati spazializzati (controllo core)."""
    station: Series[str]
    latitude: Series[float] = pa.Field(ge=-90, le=90)
    longitude: Series[float] = pa.Field(ge=-180, le=180)

    class Config:
        coerce = True
        strict = "filter"

DUCKDB_PATH = get_project_root() / "data" / "db" / "seismic_output.duckdb"

def get_file_hash(file_path: Path) -> str:
    """Calcola l'hash SHA256 di un file."""
    if not file_path or not file_path.exists():
        return "N/A"
    h = hashlib.sha256()
    h.update(file_path.read_bytes())
    return h.hexdigest()

def initialize_db_schema(con: duckdb.DuckDBPyConnection):
    """Inizializza lo schema del database DuckDB."""
    logger.info(f"Inizializzazione schema per {DUCKDB_PATH}...")
    con.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id VARCHAR PRIMARY KEY,
            run_name VARCHAR,
            run_timestamp TIMESTAMP,
            source_type VARCHAR,
            notes VARCHAR,
            pipeline_version VARCHAR,
            config_hash VARCHAR,
            raw_deltas_hash VARCHAR
        );

        CREATE TABLE IF NOT EXISTS stations (
            station_code VARCHAR PRIMARY KEY,
            network VARCHAR,
            latitude DOUBLE,
            longitude DOUBLE, 
            easting DOUBLE,
            northing DOUBLE,
            elevation DOUBLE,
            location VARCHAR
        );

        CREATE TABLE IF NOT EXISTS raw_deltas (
            run_id VARCHAR,
            event_id VARCHAR,
            network VARCHAR,
            station_code VARCHAR,
            channel VARCHAR,
            arrival_epoch DOUBLE,
            arrival_iso TIMESTAMP,
            event_reference_epoch DOUBLE,
            delta_seconds DOUBLE,
            PRIMARY KEY (run_id, event_id, station_code, channel, arrival_epoch)
        );

        CREATE TABLE IF NOT EXISTS station_stats (
            run_id VARCHAR,
            station_code VARCHAR,
            reference_date TIMESTAMP,
            base_count INTEGER,
            base_mean DOUBLE,
            base_std DOUBLE,
            base_median DOUBLE,
            soft_count INTEGER,
            soft_mean DOUBLE,
            soft_std DOUBLE,
            soft_median DOUBLE,
            soft_minus_base_mean DOUBLE,
            PRIMARY KEY (run_id, station_code, reference_date)
        );

        CREATE TABLE IF NOT EXISTS deltas_spatial (
            run_id VARCHAR,
            station_code VARCHAR,
            reference_date TIMESTAMP,
            delta_type VARCHAR,
            delta_value DOUBLE,
            easting DOUBLE,
            northing DOUBLE,
            PRIMARY KEY (run_id, station_code, reference_date, delta_type)
        );

        CREATE TABLE IF NOT EXISTS anomalies (
            run_id VARCHAR,
            station_code VARCHAR,
            timestamp TIMESTAMP,
            anomaly_type VARCHAR,
            threshold DOUBLE,
            observed_value DOUBLE,
            severity VARCHAR,
            notes VARCHAR,
            PRIMARY KEY (run_id, station_code, timestamp, anomaly_type)
        );

        CREATE TABLE IF NOT EXISTS ml_features_timeseries (
            timestamp TIMESTAMP,
            numero_eventi DOUBLE,
            energia_max DOUBLE,
            energia_media DOUBLE,
            energia_std DOUBLE,
            eventi_ultime_6h DOUBLE,
            energia_max_ultime_6h DOUBLE,
            energia_media_ultime_6h DOUBLE,
            eventi_ultime_12h DOUBLE,
            energia_max_ultime_12h DOUBLE,
            energia_media_ultime_12h DOUBLE,
            eventi_ultime_24h DOUBLE,
            energia_max_ultime_24h DOUBLE,
            energia_media_ultime_24h DOUBLE,
            eventi_ultime_48h DOUBLE,
            energia_max_ultime_48h DOUBLE,
            energia_media_ultime_48h DOUBLE,
            ora_del_giorno INTEGER,
            giorno_della_settimana INTEGER,
            is_notte INTEGER,
            is_weekend INTEGER,
            bvalue_rolling_24h DOUBLE,
            Target_Allarme INTEGER,
            PRIMARY KEY (timestamp)
        );
    """)
    logger.info("Schema database verificato/creato.")

def validate_dataframe(df: pd.DataFrame, schema: pa.DataFrameSchema, file_name: str) -> pd.DataFrame:
    """Applica uno schema Pandera a un DataFrame e gestisce gli errori."""
    if df.empty:
        logger.warning(f"DataFrame per {file_name} è vuoto, validazione saltata.")
        return df
    try:
        logger.info(f"Validazione schema per {file_name}...")
        validated_df = schema.validate(df, lazy=True)
        logger.info(f"✅ Schema per {file_name} valido.")
        return validated_df
    except pa.errors.SchemaErrors as err:
        logger.error(f"❌ Validazione schema fallita per {file_name}:")
        # Logga solo i primi 5 errori per non inondare i log
        failure_cases_summary = err.failure_cases.head(5)
        logger.error(f"\n{failure_cases_summary.to_string(index=False)}")
        raise

def _delete_existing_run_data(con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """
    Rende l'ingestione idempotente: elimina i record già presenti per run_id
    nelle tabelle figlie prima del reinserimento.
    """
    tables = [
        "raw_deltas",
        "station_stats",
        "deltas_spatial",
        "anomalies",
        "waveform_inventory",
    ]
    for t in tables:
        try:
            con.execute(f"DELETE FROM {t} WHERE run_id = ?", [run_id])
        except Exception:
            # Tabella non presente nello schema corrente: ignora
            pass

def create_or_replace_ml_view(con: duckdb.DuckDBPyConnection):
    """
    Crea o rimpiazza la vista SQL per le feature di Machine Learning.
    Questa vista aggrega i dati grezzi dei delta in una serie temporale oraria,
    pronta per essere usata dal modello.
    """
    config = load_config()
    target_threshold = config.get("ml_feature_eng", {}).get("target_alarm_threshold", 15.0)

    logger.info("Aggiornamento della vista 'ml_features_ready_view' per unire dati nuovi e storici...")
    
    # NOTA: Questa query è il "cuore" del feature engineering.
    # Centralizzarla qui la rende l'unica fonte di verità per la preparazione dei dati di training.
    view_sql = f"""
    CREATE OR REPLACE VIEW ml_features_ready_view AS
    WITH
    max_ts AS (
        SELECT date_trunc('hour', max(arrival_iso)) AS max_arrival_hour
        FROM raw_deltas
    ),
    generated_features AS (
        WITH hourly_aggregates AS (
            SELECT
                date_trunc('hour', arrival_iso) AS timestamp,
                count(*) AS numero_eventi,
                max(delta_seconds) AS energia_max,
                avg(delta_seconds) AS energia_media,
                stddev_pop(delta_seconds) AS energia_std
            FROM raw_deltas
            GROUP BY date_trunc('hour', arrival_iso)
        ),
        rolling_features AS (
            SELECT
                *,
                sum(numero_eventi) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS eventi_ultime_6h,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS energia_max_ultime_6h,
                avg(energia_media) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS energia_media_ultime_6h,
                sum(numero_eventi) OVER (ORDER BY timestamp ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS eventi_ultime_12h,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS energia_max_ultime_12h,
                avg(energia_media) OVER (ORDER BY timestamp ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS energia_media_ultime_12h,
                sum(numero_eventi) OVER (ORDER BY timestamp ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS eventi_ultime_24h,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS energia_max_ultime_24h,
                avg(energia_media) OVER (ORDER BY timestamp ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS energia_media_ultime_24h,
                sum(numero_eventi) OVER (ORDER BY timestamp ROWS BETWEEN 47 PRECEDING AND CURRENT ROW) AS eventi_ultime_48h,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 47 PRECEDING AND CURRENT ROW) AS energia_max_ultime_48h,
                avg(energia_media) OVER (ORDER BY timestamp ROWS BETWEEN 47 PRECEDING AND CURRENT ROW) AS energia_media_ultime_48h
            FROM hourly_aggregates
        ),
        target_generation AS (
            SELECT
                *,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 1 FOLLOWING AND 24 FOLLOWING) AS max_energia_futura,
            CASE WHEN max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 1 FOLLOWING AND 24 FOLLOWING) > {target_threshold} THEN 1 ELSE 0 END AS Target_Allarme
            FROM rolling_features
        )
        SELECT
            timestamp,
            COALESCE(numero_eventi, 0) as numero_eventi, COALESCE(energia_max, 0) as energia_max, COALESCE(energia_media, 0) as energia_media, COALESCE(energia_std, 0) as energia_std,
            COALESCE(eventi_ultime_6h, 0) as eventi_ultime_6h, COALESCE(energia_max_ultime_6h, 0) as energia_max_ultime_6h, COALESCE(energia_media_ultime_6h, 0) as energia_media_ultime_6h,
            COALESCE(eventi_ultime_12h, 0) as eventi_ultime_12h, COALESCE(energia_max_ultime_12h, 0) as energia_max_ultime_12h, COALESCE(energia_media_ultime_12h, 0) as energia_media_ultime_12h,
            COALESCE(eventi_ultime_24h, 0) as eventi_ultime_24h, COALESCE(energia_max_ultime_24h, 0) as energia_max_ultime_24h, COALESCE(energia_media_ultime_24h, 0) as energia_media_ultime_24h,
            COALESCE(eventi_ultime_48h, 0) as eventi_ultime_48h, COALESCE(energia_max_ultime_48h, 0) as energia_max_ultime_48h, COALESCE(energia_media_ultime_48h, 0) as energia_media_ultime_48h,
            extract('hour' from timestamp) as ora_del_giorno,
            extract('dow' from timestamp) as giorno_della_settimana,
            CASE WHEN extract('hour' from timestamp) >= 22 OR extract('hour' from timestamp) <= 6 THEN 1 ELSE 0 END AS is_notte,
            CASE WHEN extract('dow' from timestamp) IN (0, 6) THEN 1 ELSE 0 END AS is_weekend,
            NULL as bvalue_rolling_24h,
            Target_Allarme
        FROM target_generation
        WHERE timestamp < (SELECT max_arrival_hour FROM max_ts) - INTERVAL '24' HOUR
    )
    SELECT 
        timestamp,
        COALESCE(numero_eventi, 0) as numero_eventi, COALESCE(energia_max, 0) as energia_max, COALESCE(energia_media, 0) as energia_media, COALESCE(energia_std, 0) as energia_std,
        COALESCE(eventi_ultime_6h, 0) as eventi_ultime_6h, COALESCE(energia_max_ultime_6h, 0) as energia_max_ultime_6h, COALESCE(energia_media_ultime_6h, 0) as energia_media_ultime_6h,
        COALESCE(eventi_ultime_12h, 0) as eventi_ultime_12h, COALESCE(energia_max_ultime_12h, 0) as energia_max_ultime_12h, COALESCE(energia_media_ultime_12h, 0) as energia_media_ultime_12h,
        COALESCE(eventi_ultime_24h, 0) as eventi_ultime_24h, COALESCE(energia_max_ultime_24h, 0) as energia_max_ultime_24h, COALESCE(energia_media_ultime_24h, 0) as energia_media_ultime_24h,
        COALESCE(eventi_ultime_48h, 0) as eventi_ultime_48h, COALESCE(energia_max_ultime_48h, 0) as energia_max_ultime_48h, COALESCE(energia_media_ultime_48h, 0) as energia_media_ultime_48h,
        extract('hour' from timestamp) as ora_del_giorno,
        extract('dow' from timestamp) as giorno_della_settimana,
        CASE WHEN extract('hour' from timestamp) >= 22 OR extract('hour' from timestamp) <= 6 THEN 1 ELSE 0 END AS is_notte,
        CASE WHEN extract('dow' from timestamp) IN (0, 6) THEN 1 ELSE 0 END AS is_weekend,
        bvalue_rolling_24h,
        Target_Allarme
    FROM ml_features_timeseries

    UNION ALL

    SELECT 
        timestamp, numero_eventi, energia_max, energia_media, energia_std,
        eventi_ultime_6h, energia_max_ultime_6h, energia_media_ultime_6h,
        eventi_ultime_12h, energia_max_ultime_12h, energia_media_ultime_12h,
        eventi_ultime_24h, energia_max_ultime_24h, energia_media_ultime_24h,
        eventi_ultime_48h, energia_max_ultime_48h, energia_media_ultime_48h,
        ora_del_giorno, giorno_della_settimana, is_notte, is_weekend,
        bvalue_rolling_24h, Target_Allarme
    FROM generated_features;
    """
    con.execute(view_sql)
    logger.info("✅ Vista 'ml_features_ready_view' creata/aggiornata con successo.")

def ingest_run_data(run_id: str, run_name: str, run_dir: Path, source_type: str, notes: str = None, config_path: Path = None):
    """Ingerisce i dati di una singola run nel database DuckDB."""
    logger.info(f"Inizio ingestione dati per run '{run_name}' (ID: {run_id})...")

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        initialize_db_schema(con)

        # Transazione unica: o tutto o niente
        con.execute("BEGIN TRANSACTION")

        # 1) Cleanup idempotente
        _delete_existing_run_data(con, run_id)
        logger.info(f"Pulizia record esistenti completata per run_id={run_id}.")

        # 2) Upsert metadati run
        run_timestamp = datetime.now(timezone.utc)
        config_hash = get_file_hash(config_path) if config_path else "N/A"
        
        # Trova il file dei delta per calcolare l'hash
        deltas_path = next(run_dir.glob("interim/station_deltas*"), None)
        raw_deltas_hash = get_file_hash(deltas_path) if deltas_path else "N/A"

        con.execute("""
            INSERT INTO runs (run_id, run_name, run_timestamp, source_type, notes, config_hash, raw_deltas_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                run_name = EXCLUDED.run_name,
                run_timestamp = EXCLUDED.run_timestamp,
                source_type = EXCLUDED.source_type,
                notes = EXCLUDED.notes,
                config_hash = EXCLUDED.config_hash,
                raw_deltas_hash = EXCLUDED.raw_deltas_hash;
        """, [run_id, run_name, run_timestamp, source_type, notes, config_hash, raw_deltas_hash])
        logger.info(f"Metadati run '{run_name}' inseriti/aggiornati.")

        # 3) Ingest data from run files, with validation
        
        # --- RAW DELTAS ---
        if deltas_path and deltas_path.exists():
            df_deltas = pd.read_csv(deltas_path)
            # Gestisce l'ingestione sia da file con 'station' che 'station_code'
            if 'station_code' in df_deltas.columns and 'station' not in df_deltas.columns:
                df_deltas = df_deltas.rename(columns={'station_code': 'station'})
            df_deltas = validate_dataframe(df_deltas, RawDeltasSchema, deltas_path.name)
            
            df_deltas['run_id'] = run_id
            df_deltas = df_deltas.rename(columns={'station': 'station_code'})
            df_deltas['arrival_iso'] = pd.to_datetime(df_deltas['arrival_iso'], errors='coerce')
            
            con.execute("INSERT INTO raw_deltas BY NAME SELECT * FROM df_deltas")
            logger.info(f"Inseriti {len(df_deltas)} record in raw_deltas.")
        else:
            logger.warning(f"File delta non trovato in {run_dir / 'interim'}. Salto ingestione raw_deltas.")

        # --- STATION STATS ---
        stats_path = run_dir / "processed" / "station_stats.csv"
        if stats_path.exists():
            df_stats = pd.read_csv(stats_path)
            # Gestisce l'ingestione sia da file con 'station' che 'station_code'
            if 'station_code' in df_stats.columns and 'station' not in df_stats.columns:
                df_stats = df_stats.rename(columns={'station_code': 'station'})
            df_stats = validate_dataframe(df_stats, StationStatsSchema, stats_path.name)

            df_stats['run_id'] = run_id
            df_stats = df_stats.rename(columns={'station': 'station_code'})
            df_stats['reference_date'] = pd.to_datetime(df_stats['reference_date'], errors='coerce')

            con.execute("INSERT INTO station_stats BY NAME SELECT * FROM df_stats")
            logger.info(f"Inseriti {len(df_stats)} record in station_stats.")
        else:
            logger.warning(f"File station_stats.csv non trovato. Salto ingestione station_stats.")

        # --- STATIONS and DELTAS_SPATIAL ---
        spatial_path = run_dir / "processed" / "deltas_spatial.csv"
        if spatial_path.exists():
            df_spatial = pd.read_csv(spatial_path)
            # Gestisce l'ingestione sia da file con 'station' che 'station_code'
            if 'station_code' in df_spatial.columns and 'station' not in df_spatial.columns:
                df_spatial = df_spatial.rename(columns={'station_code': 'station'})
            df_spatial = validate_dataframe(df_spatial, DeltasSpatialSchema, spatial_path.name)

            # Ingestione STATIONS
            station_cols_map = {
                'station': 'station_code', 'network': 'network', 'latitude': 'latitude',
                'longitude': 'longitude', 'easting': 'easting', 'northing': 'northing',
                'elevation': 'elevation', 'location': 'location'
            }
            
            available_cols = [col for col in station_cols_map if col in df_spatial.columns]
            
            if 'station' in available_cols:
                df_stations = df_spatial[available_cols].copy()
                df_stations = df_stations.drop_duplicates(subset=['station'])
                df_stations = df_stations.rename(columns=station_cols_map)
                
                # Add missing optional columns with NULL
                for db_col in station_cols_map.values():
                    if db_col not in df_stations.columns:
                        df_stations[db_col] = None
                
                final_station_cols = list(station_cols_map.values())
                df_stations = df_stations.reindex(columns=final_station_cols)

                con.execute("""
                    INSERT INTO stations BY NAME
                    SELECT * FROM df_stations
                    ON CONFLICT(station_code) DO UPDATE SET
                        network = EXCLUDED.network, latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude, easting = EXCLUDED.easting,
                        northing = EXCLUDED.northing, elevation = EXCLUDED.elevation,
                        location = EXCLUDED.location;
                """)
                logger.info(f"Inseriti/Aggiornati {len(df_stations)} record in stations.")
            else:
                logger.warning("Colonna 'station' non trovata in deltas_spatial.csv. Impossibile ingerire dati stazioni.")
            
            # Ingestione DELTAS_SPATIAL
            # Trasforma i dati da formato wide a long per la tabella deltas_spatial
            id_vars = ['station', 'reference_date']
            value_vars = [col for col in ['soft_minus_base_mean', 'base_mean'] if col in df_spatial.columns]
            
            if 'easting' in df_spatial.columns: id_vars.append('easting')
            if 'northing' in df_spatial.columns: id_vars.append('northing')

            if value_vars:
                df_deltas_spatial_melted = df_spatial.melt(
                    id_vars=id_vars, value_vars=value_vars,
                    var_name='delta_type', value_name='delta_value'
                ).dropna(subset=['delta_value'])

                if not df_deltas_spatial_melted.empty:
                    df_deltas_spatial_melted['run_id'] = run_id
                    df_deltas_spatial_melted = df_deltas_spatial_melted.rename(columns={'station': 'station_code'})
                    df_deltas_spatial_melted['reference_date'] = pd.to_datetime(df_deltas_spatial_melted['reference_date'], errors='coerce')
                    
                    con.execute("INSERT INTO deltas_spatial BY NAME SELECT * FROM df_deltas_spatial_melted")
                    logger.info(f"Inseriti {len(df_deltas_spatial_melted)} record in deltas_spatial.")
        else:
            logger.warning(f"File 'deltas_spatial.csv' non trovato. Salto ingestione stazioni e deltas_spatial.")

        # 4) Refresh vista ML
        create_or_replace_ml_view(con)

        con.execute("COMMIT")
        logger.info(f"Ingestione per run '{run_name}' completata con successo.")

    except Exception:
        con.execute("ROLLBACK")
        logger.exception(f"Errore ingestione per run_id={run_id}. Rollback eseguito.")
        raise
    finally:
        con.close()

def main():
    parser = argparse.ArgumentParser(description="Ingerisce i dati di una run nel database DuckDB.")
    parser.add_argument("--run-id", type=str, required=True, help="ID univoco dell'esecuzione.")
    parser.add_argument("--run-name", type=str, required=True, help="Nome dell'esecuzione.")
    parser.add_argument("--run-dir", type=Path, required=True, help="Percorso alla directory della run.")
    parser.add_argument("--source-type", type=str, default="mseed", help="Tipo di sorgente dati (es. mseed, catalog).")
    parser.add_argument("--config-path", type=Path, help="Percorso al file di configurazione usato per la run.")
    parser.add_argument("--notes", type=str, default=None, help="Note aggiuntive sulla run.")
    args = parser.parse_args()

    ingest_run_data(args.run_id, args.run_name, args.run_dir, args.source_type, args.notes, args.config_path)

if __name__ == "__main__":
    main()