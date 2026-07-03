import argparse
import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
import sys
import hashlib

# Aggiungiamo la root del progetto al path per risolvere le dipendenze
# Questo non è ideale, ma per uno script è una soluzione pragmatica.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils import setup_logger, load_config

logger = setup_logger("ingest_to_db")

DUCKDB_PATH = PROJECT_ROOT / "data" / "db" / "seismic_output.duckdb"

def get_file_hash(file_path: Path) -> str:
    """Calcola l'hash SHA256 di un file."""
    if not file_path.exists():
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

def create_or_replace_ml_view(con: duckdb.DuckDBPyConnection):
    """
    Crea o rimpiazza la vista SQL per le feature di Machine Learning.
    Questa vista aggrega i dati grezzi dei delta in una serie temporale oraria,
    pronta per essere usata dal modello.
    """
    logger.info("Aggiornamento della vista 'ml_features_ready_view' per unire dati nuovi e storici...")
    
    # NOTA: Questa query è il "cuore" del feature engineering.
    # Centralizzarla qui la rende l'unica fonte di verità per la preparazione dei dati di training.
    view_sql = """
    CREATE OR REPLACE VIEW ml_features_ready_view AS
    WITH generated_features AS (
        -- Questa parte genera le feature dai dati grezzi (raw_deltas), come prima.
        WITH hourly_aggregates AS (
            -- 1. Aggrega i rilevamenti grezzi in bucket orari
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
            -- 2. Calcola le feature su finestre mobili
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
            -- 3. Genera il target cercando eventi significativi nel futuro
            SELECT
                *,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 1 FOLLOWING AND 24 FOLLOWING) AS max_energia_futura,
                CASE WHEN max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 1 FOLLOWING AND 24 FOLLOWING) > 15.0 THEN 1 ELSE 0 END AS Target_Allarme
            FROM rolling_features
        )
        -- 4. Seleziona le colonne finali e aggiunge feature temporali
        SELECT
            'generated' as source, -- Aggiungiamo una colonna per tracciare l'origine
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
            NULL as bvalue_rolling_24h, -- Manteniamo la colonna per compatibilità, ma la calcoleremo in Python
            Target_Allarme
        FROM target_generation
        -- Escludiamo le ultime 24 ore dove non possiamo calcolare un target affidabile
        WHERE timestamp < (SELECT max(timestamp) FROM raw_deltas) - INTERVAL '24' HOUR
    )
    -- Seleziona tutte le colonne dalla tabella delle feature storiche
    -- e le unisce con quelle generate al momento.
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
        CASE WHEN extract('dow' from timestamp) IN (0, 6) THEN 1 ELSE 0 END AS is_weekend, -- DuckDB DOW: 0=Sun, 6=Sat
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

    # Assicuriamoci che la directory del database esista prima di connetterci.
    # DuckDB può creare il file, ma non le directory parent.
    db_dir = DUCKDB_PATH.parent
    if not db_dir.exists():
        logger.info(f"La directory del database non esiste. La creo in: {db_dir}")
        db_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(database=str(DUCKDB_PATH), read_only=False)
    initialize_db_schema(con)

    # Ingestione raw_deltas
    raw_deltas_path = run_dir / "interim" / "station_deltas_from_mseed.csv"
    if not raw_deltas_path.exists():
        raw_deltas_path = run_dir / "interim" / "station_deltas.csv"

    if raw_deltas_path.exists():
        # Calcola hash del file di input e della configurazione
        raw_deltas_hash = get_file_hash(raw_deltas_path)
        config_hash = get_file_hash(config_path) if config_path else "N/A"

        # Inserisci metadati della run
        con.execute(f"""
            INSERT INTO runs (run_id, run_name, run_timestamp, source_type, notes, pipeline_version, config_hash, raw_deltas_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO UPDATE SET
                run_name = EXCLUDED.run_name,
                run_timestamp = EXCLUDED.run_timestamp,
                source_type = EXCLUDED.source_type,
                notes = EXCLUDED.notes,
                pipeline_version = EXCLUDED.pipeline_version,
                config_hash = EXCLUDED.config_hash,
                raw_deltas_hash = EXCLUDED.raw_deltas_hash;
        """, [run_id, run_name, datetime.now(timezone.utc), source_type, notes, "1.0.0", config_hash, raw_deltas_hash])
        logger.info(f"Metadati run '{run_name}' inseriti/aggiornati.")

        df_deltas = pd.read_csv(raw_deltas_path)
        df_deltas['run_id'] = run_id
        # Assicuriamoci che le colonne siano nell'ordine corretto per l'inserimento
        # e che i tipi di dato siano corretti.
        df_deltas['arrival_iso'] = pd.to_datetime(df_deltas['arrival_iso'], errors='coerce')
        df_deltas.rename(columns={'station': 'station_code'}, inplace=True)
        
        # Seleziona e ordina esplicitamente le colonne per l'inserimento
        cols_to_insert = [
            'run_id', 'event_id', 'network', 'station_code', 'channel', 
            'arrival_epoch', 'arrival_iso', 'event_reference_epoch', 'delta_seconds'
        ]
        df_deltas_insert = df_deltas[cols_to_insert]

        # Inserimento esplicito delle colonne
        con.execute(f"""
            INSERT INTO raw_deltas (
                run_id, event_id, network, station_code, channel, 
                arrival_epoch, arrival_iso, event_reference_epoch, delta_seconds
            ) 
            SELECT * FROM df_deltas_insert 
            ON CONFLICT DO NOTHING;
        """)
        logger.info(f"Inseriti {len(df_deltas)} record in raw_deltas.")
    else:
        logger.warning(f"File raw_deltas non trovato in {run_dir}. Salto ingestione raw_deltas.")

    # Ingestione station_stats
    station_stats_path = run_dir / "processed" / "station_stats.csv"
    if station_stats_path.exists():
        df_stats = pd.read_csv(station_stats_path)
        df_stats.rename(columns={'station': 'station_code'}, inplace=True)
        df_stats['run_id'] = run_id
        # La data di riferimento è la data della run
        df_stats['reference_date'] = pd.to_datetime(datetime.now(timezone.utc).date())

        # Ordine esplicito delle colonne per l'inserimento
        stats_cols_ordered = [
            'run_id', 'station_code', 'reference_date', 'base_count', 'base_mean', 'base_std', 
            'base_median', 'soft_count', 'soft_mean', 'soft_std', 'soft_median', 
            'soft_minus_base_mean'
        ]
        # Alcune colonne potrebbero non esistere (es. se non c'è una soft run)
        # Selezioniamo solo le colonne esistenti nel DataFrame
        existing_cols = [col for col in stats_cols_ordered if col in df_stats.columns]
        df_stats_insert = df_stats[existing_cols]

        # Creiamo la stringa di colonne per la query SQL
        cols_sql_str = ", ".join(existing_cols)
        con.execute(f"INSERT INTO station_stats ({cols_sql_str}) SELECT * FROM df_stats_insert ON CONFLICT DO NOTHING;")
        logger.info(f"Inseriti {len(df_stats)} record in station_stats.")
    else:
        logger.warning(f"File station_stats non trovato in {run_dir}. Salto ingestione station_stats.")

    # Ingestione/Aggiornamento stations
    deltas_spatial_path = run_dir / "processed" / "deltas_spatial.csv"
    if deltas_spatial_path.exists():
        df_spatial = pd.read_csv(deltas_spatial_path)
        # Selezioniamo solo le colonne relative alle stazioni e rimuoviamo i duplicati
        df_stations = df_spatial[[
            'station', 'network', 'latitude', 'longitude', 
            'easting', 'northing', 'elevation', 'location'
        ]].drop_duplicates(subset=['station']).rename(columns={'station': 'station_code'})
        
        if not df_stations.empty:
            # Inserimento esplicito per robustezza
            con.execute("""
                INSERT INTO stations (station_code, network, latitude, longitude, easting, northing, elevation, location)
                SELECT station_code, network, latitude, longitude, easting, northing, elevation, location FROM df_stations
                ON CONFLICT(station_code) DO UPDATE SET 
                    network=EXCLUDED.network, 
                    latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude, 
                    easting=EXCLUDED.easting, northing=EXCLUDED.northing, 
                    elevation=EXCLUDED.elevation, location=EXCLUDED.location;
            """)
            logger.info(f"Inserite/Aggiornate {len(df_stations)} stazioni nella tabella 'stations'.")

    # Ingestione deltas_spatial
    if deltas_spatial_path.exists():
        # Rileggiamo il file per sicurezza
        df_spatial_full = pd.read_csv(deltas_spatial_path)
        df_spatial_full['run_id'] = run_id
        df_spatial_full['reference_date'] = pd.to_datetime(datetime.now(timezone.utc).date())
        # Creiamo le righe per l'inserimento
        df_to_insert = df_spatial_full[['run_id', 'station', 'reference_date', 'base_mean', 'easting', 'northing']].copy()
        df_to_insert.rename(columns={'station': 'station_code', 'base_mean': 'delta_value'}, inplace=True)
        df_to_insert['delta_type'] = 'base_mean'
        
        # Inserimento esplicito
        con.execute("""
            INSERT INTO deltas_spatial (run_id, station_code, reference_date, delta_type, delta_value, easting, northing)
            SELECT run_id, station_code, reference_date, delta_type, delta_value, easting, northing FROM df_to_insert
            ON CONFLICT DO NOTHING;
        """)
        logger.info(f"Inseriti {len(df_to_insert)} record in deltas_spatial.")
    else:
        logger.warning(f"File deltas_spatial non trovato in {run_dir}. Salto ingestione deltas_spatial.")

    # TODO: Ingestione anomalies (richiede che lo script di allerta le salvi in un CSV standard)
    # Per ora, lo schema è pronto, ma l'ingestione è da implementare quando gli script di allerta produrranno un output standard.

    # Dopo aver ingerito i dati, aggiorniamo la vista per il ML
    create_or_replace_ml_view(con)

    con.close()
    logger.info(f"Ingestione per run '{run_name}' completata con successo.")

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