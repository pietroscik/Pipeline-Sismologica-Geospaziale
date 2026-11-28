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

        CREATE TABLE IF NOT EXISTS waveform_energy_hourly (
            -- Statistica energetica oraria per stazione (RSAM-like), output
            -- di compute_mseed_deltas.py: RMS e picco di ampiezza della
            -- traccia pre-processata (banda di detection). E' l'energia
            -- REALE del segnale, assente nei soli conteggi di trigger.
            station_code VARCHAR,
            channel VARCHAR,
            hour TIMESTAMP,
            rms DOUBLE,
            peak DOUBLE,
            PRIMARY KEY (station_code, channel, hour)
        );

        CREATE TABLE IF NOT EXISTS catalog_events (
            -- Catalogo eventi INGV (fdsnws/event): ground truth del
            -- Target_Allarme. Popolata da download_cf_events.py + ingest.
            event_id VARCHAR PRIMARY KEY,
            time TIMESTAMP,
            latitude DOUBLE,
            longitude DOUBLE,
            depth_km DOUBLE,
            mag_type VARCHAR,
            magnitude DOUBLE,
            event_location_name VARCHAR,
            event_type VARCHAR
        );
    """)
    # La tabella storica puo' esistere gia' senza le colonne del catalogo:
    # ALTER ... IF NOT EXISTS non e' portabile su tutte le versioni DuckDB,
    # si usa un fallback silenzioso se la colonna esiste gia'.
    for _col_sql in (
        "ALTER TABLE ml_features_timeseries ADD COLUMN eventi_catalogo_6h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN magnitudo_max_6h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN rms_medio DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN rms_max DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN picco_max DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN rms_max_ultime_6h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN rms_max_ultime_24h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN net_rms_log_ratio_med DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN net_peak_log_ratio_med DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN n_stations_energy DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN frac_stations_2x_rms DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN net_rms_log_ratio_6h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN net_rms_log_ratio_max_24h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN frac_stations_2x_6h DOUBLE",
        "ALTER TABLE ml_features_timeseries ADD COLUMN frac_stations_2x_max_24h DOUBLE",
    ):
        try:
            con.execute(_col_sql)
        except Exception:
            pass  # colonna gia' presente
    logger.info("Schema database verificato/creato.")

def get_catalog_target_params() -> dict:
    """Parametri del target da catalogo (config.yaml > default).

    Target_Allarme = 1 per l'ora t se il catalogo INGV contiene almeno un
    evento con magnitude >= mag_threshold entro horizon_hours da t.
    Tarato il 2026-11-28 sul catalogo reale dei Campi Flegrei (950 eventi nel
    mese, M 0.2-5.4): M>=3.5 con H=6h dà l'11.7% di ore positive.
    """
    catalog_cfg = load_config().get("catalog", {}) or {}
    return {
        "mag_threshold": float(catalog_cfg.get("mag_threshold", 3.5)),
        "horizon_hours": int(catalog_cfg.get("horizon_hours", 6)),
    }


def create_or_replace_ml_view(con: duckdb.DuckDBPyConnection):
    """
    Crea o rimpiazza la vista SQL per le feature di Machine Learning.
    Questa vista aggrega i dati grezzi dei delta in una serie temporale oraria,
    pronta per essere usata dal modello.
    """
    logger.info("Aggiornamento della vista 'ml_features_ready_view' per unire dati nuovi e storici...")

    # Vista energetica ROBUSTA e CAUSALE: normalizza RMS e picco di ogni
    # stazione per la mediana delle 168 ore PRECEDENTI (1 settimana causale,
    # ROWS ... 1 PRECEDING: mai il presente). Rimuove gli effetti di scala
    # non stazionari (gain strumento, rumore di fondo a lunga correlazione)
    # e quelli meteo/antropici periodici che rendevano rms_medio/rms_max non
    # confrontabili tra ore diverse. Il log-rapporto >= 0.301 (10^0.3 ~ 2x)
    # marca le stazioni con energia raddoppiata rispetto alla base settimanale.
    robust_energy_sql = """
    CREATE OR REPLACE VIEW waveform_energy_robust_hourly AS
    WITH hourly_station AS (
        SELECT
            DATE_TRUNC('hour', hour) AS hour_ts,
            station_code,
            AVG(rms) AS rms,
            MAX(peak) AS peak
        FROM waveform_energy_hourly
        WHERE rms > 0 AND peak > 0
        GROUP BY 1, 2
    ),
    causal_norm AS (
        SELECT
            hour_ts,
            station_code,
            rms,
            peak,
            MEDIAN(rms) OVER (
                PARTITION BY station_code
                ORDER BY hour_ts
                ROWS BETWEEN 168 PRECEDING AND 1 PRECEDING
            ) AS base_rms_7d,
            MEDIAN(peak) OVER (
                PARTITION BY station_code
                ORDER BY hour_ts
                ROWS BETWEEN 168 PRECEDING AND 1 PRECEDING
            ) AS base_peak_7d
        FROM hourly_station
    ),
    ratios AS (
        SELECT
            hour_ts,
            station_code,
            LOG10(rms / GREATEST(base_rms_7d, 1e-9)) AS rms_log_ratio,
            LOG10(peak / GREATEST(base_peak_7d, 1e-9)) AS peak_log_ratio
        FROM causal_norm
        WHERE base_rms_7d IS NOT NULL
    )
    SELECT
        hour_ts,
        COUNT(DISTINCT station_code) AS n_stations_energy,
        MEDIAN(rms_log_ratio) AS net_rms_log_ratio_med,
        MEDIAN(peak_log_ratio) AS net_peak_log_ratio_med,
        SUM(CASE WHEN rms_log_ratio >= 0.301 THEN 1 ELSE 0 END) AS n_stations_2x_rms,
        AVG(CASE WHEN rms_log_ratio >= 0.301 THEN 1.0 ELSE 0.0 END) AS frac_stations_2x_rms
    FROM ratios
    GROUP BY hour_ts;
    """
    con.execute(robust_energy_sql)

    target_params = get_catalog_target_params()
    mag_threshold = target_params["mag_threshold"]
    horizon_hours = target_params["horizon_hours"]
    logger.info(
        f"Target da catalogo: magnitudine >= {mag_threshold}, orizzonte {horizon_hours}h "
        f"(se la tabella catalog_events è vuota il target resta 0)"
    )

    # NOTA: Questa query è il "cuore" del feature engineering.
    # Centralizzarla qui la rende l'unica fonte di verità per la preparazione dei dati di training.
    view_sql = """
    CREATE OR REPLACE VIEW ml_features_ready_view AS
    WITH generated_features AS (
        -- Questa parte genera le feature dai dati grezzi (raw_deltas), come prima.
        WITH hourly_grid AS (
            -- Griglia oraria CONTINUA: genera anche le ore senza dati, così i
            -- buchi di trasmissione diventano righe esplicite (numero_eventi=0)
            -- invece di assenze silenziose. Le finestre mobili contano quindi
            -- ore reali e non solo ore con dati.
            -- NOTA: generate_series in DuckDB è una funzione TABELLA (FROM),
            -- non scalare: usata nel SELECT ritornerebbe una LISTA in una riga.
            SELECT grid.ts AS timestamp
            FROM generate_series(
                       date_trunc('hour', (SELECT min(arrival_iso) FROM raw_deltas)),
                       date_trunc('hour', (SELECT max(arrival_iso) FROM raw_deltas)),
                       INTERVAL 1 HOUR
                   ) AS grid(ts)
        ),
        hourly_counts AS (
            -- 1. Aggrega i rilevamenti grezzi in bucket orari
            SELECT
                date_trunc('hour', arrival_iso) AS hour,
                count(*) AS numero_eventi,
                count(DISTINCT station_code) AS stazioni_attive,
                max(delta_seconds) AS energia_max,
                avg(delta_seconds) AS energia_media,
                stddev_pop(delta_seconds) AS energia_std
            FROM raw_deltas
            GROUP BY date_trunc('hour', arrival_iso)
        ),
        expected_stations AS (
            -- Numero di stazioni che hanno mai trasmesso nel dataset: la
            -- completezza è relativa a questo valore (autonoma dal catalogo
            -- 'stations', che può contenere stazioni fuori periodo)
            SELECT count(DISTINCT station_code) AS stazioni_attese FROM raw_deltas
        ),
        catalog_hourly AS (
            -- Aggregato orario del catalogo eventi INGV: base per le feature
            -- esplicative (finestre PASSATE) e per il Target_Allarme (FUTURO).
            SELECT
                date_trunc('hour', time) AS hour,
                count(*) AS catalog_n,
                max(magnitude) AS catalog_max
            FROM catalog_events
            GROUP BY date_trunc('hour', time)
        ),
        hourly_aggregates AS (
            -- Griglia + conteggi: le ore mancanti diventano righe con
            -- numero_eventi=0 e is_data_complete=0
            SELECT
                g.timestamp AS timestamp,
                COALESCE(c.numero_eventi, 0) AS numero_eventi,
                COALESCE(c.energia_max, 0) AS energia_max,
                COALESCE(c.energia_media, 0) AS energia_media,
                COALESCE(c.energia_std, 0) AS energia_std,
                ch.catalog_n AS catalog_n,
                ch.catalog_max AS catalog_max,
                COALESCE(r.net_rms_log_ratio_med, 0) AS net_rms_log_ratio_med,
                COALESCE(r.net_peak_log_ratio_med, 0) AS net_peak_log_ratio_med,
                COALESCE(r.n_stations_energy, 0) AS n_stations_energy,
                COALESCE(r.frac_stations_2x_rms, 0) AS frac_stations_2x_rms,
                CASE
                    WHEN e.stazioni_attese = 0 THEN 0
                    WHEN COALESCE(c.stazioni_attive, 0)::DOUBLE / e.stazioni_attese >= 0.95 THEN 1
                    ELSE 0
                END AS is_data_complete
            FROM hourly_grid g
            CROSS JOIN expected_stations e
            LEFT JOIN hourly_counts c ON c.hour = g.timestamp
            LEFT JOIN catalog_hourly ch ON ch.hour = g.timestamp
            LEFT JOIN waveform_energy_robust_hourly r ON r.hour_ts = g.timestamp
        ),
        rolling_features AS (
            -- 2. Calcola le feature su finestre mobili. Le feature del
            -- catalogo (eventi_catalogo_6h, magnitudo_max_6h) guardano solo
            -- al PASSATO: mai data leakage dal target nel training.
            SELECT
                *,
                COALESCE(sum(catalog_n) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW), 0) AS eventi_catalogo_6h,
                COALESCE(max(catalog_max) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW), 0) AS magnitudo_max_6h,
                sum(numero_eventi) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS eventi_ultime_6h,
                max(energia_max) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS energia_max_ultime_6h,
                avg(energia_media) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS energia_media_ultime_6h,
                avg(net_rms_log_ratio_med) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS net_rms_log_ratio_6h,
                max(net_rms_log_ratio_med) OVER (ORDER BY timestamp ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS net_rms_log_ratio_max_24h,
                avg(frac_stations_2x_rms) OVER (ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS frac_stations_2x_6h,
                max(frac_stations_2x_rms) OVER (ORDER BY timestamp ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS frac_stations_2x_max_24h,
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
            -- 3. Target da CATALOGO INGV (ground truth): 1 se nelle prossime
            -- H ore il catalogo contiene almeno un evento con magnitude >= M.
            -- Sostituisce il proxy max(delta_seconds) > 15.0, degenere sui
            -- dati reali (banda 7.96-9.88 s senza segnale, misurata 2026-11-28:
            -- 144 ore tutte con target 0). Se catalog_events è vuota il
            -- target resta 0 su tutto (nessun falso positivo).
            SELECT
                r.*,
                CASE WHEN EXISTS (
                    SELECT 1 FROM catalog_events e
                    WHERE e.magnitude >= {mag_threshold}
                      AND e.time > r.timestamp
                      AND e.time <= r.timestamp + INTERVAL '{horizon_hours}' HOUR
                ) THEN 1 ELSE 0 END AS Target_Allarme
            FROM rolling_features r
        ),
        data_boundary AS (
            -- Massimo timestamp dei dati grezzi: esclude le ultime H ore dove il
            -- target non è calcolabile in modo affidabile.
            -- NOTA: calcolato in un CTE separato perché DuckDB non ammette
            -- aggregati in una subquery dentro il WHERE.
            SELECT max(arrival_iso) AS max_ts FROM raw_deltas
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
            -- Completezza del dato per finestra oraria: 1 se >=95% delle
            -- stazioni attese ha trasmesso, 0 altrimenti (ore mancanti incluse).
            -- Evita che uno 0 dovuto a telemetria assente sia confuso con
            -- assenza di attività sismica.
            is_data_complete,
            net_rms_log_ratio_med,
            net_peak_log_ratio_med,
            n_stations_energy,
            frac_stations_2x_rms,
            COALESCE(net_rms_log_ratio_6h, 0) as net_rms_log_ratio_6h,
            COALESCE(net_rms_log_ratio_max_24h, 0) as net_rms_log_ratio_max_24h,
            COALESCE(frac_stations_2x_6h, 0) as frac_stations_2x_6h,
            COALESCE(frac_stations_2x_max_24h, 0) as frac_stations_2x_max_24h,
            eventi_catalogo_6h,
            magnitudo_max_6h,
            Target_Allarme
        FROM target_generation, data_boundary
        -- Escludiamo le ultime H ore dove non possiamo calcolare un target affidabile
        WHERE timestamp < data_boundary.max_ts - INTERVAL '{horizon_hours}' HOUR
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
        CAST(NULL AS INTEGER) as is_data_complete, -- storico: completezza non tracciata
        CAST(net_rms_log_ratio_med AS DOUBLE) AS net_rms_log_ratio_med, -- storico pre-energia: NULL
        CAST(net_peak_log_ratio_med AS DOUBLE) AS net_peak_log_ratio_med,
        CAST(n_stations_energy AS DOUBLE) AS n_stations_energy,
        CAST(frac_stations_2x_rms AS DOUBLE) AS frac_stations_2x_rms,
        CAST(net_rms_log_ratio_6h AS DOUBLE) AS net_rms_log_ratio_6h,
        CAST(net_rms_log_ratio_max_24h AS DOUBLE) AS net_rms_log_ratio_max_24h,
        CAST(frac_stations_2x_6h AS DOUBLE) AS frac_stations_2x_6h,
        CAST(frac_stations_2x_max_24h AS DOUBLE) AS frac_stations_2x_max_24h,
        CAST(eventi_catalogo_6h AS DOUBLE) AS eventi_catalogo_6h, -- storico pre-catalogo: NULL
        CAST(magnitudo_max_6h AS DOUBLE) AS magnitudo_max_6h,
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
        bvalue_rolling_24h, is_data_complete,
        net_rms_log_ratio_med, net_peak_log_ratio_med, n_stations_energy,
        frac_stations_2x_rms, net_rms_log_ratio_6h, net_rms_log_ratio_max_24h,
        frac_stations_2x_6h, frac_stations_2x_max_24h,
        eventi_catalogo_6h, magnitudo_max_6h, Target_Allarme
    FROM generated_features;
    """
    # I placeholder {mag_threshold}/{horizon_hours} nel SQL vengono interpolati
    # qui (float per la soglia: il valore arriva validato da config/CLI)
    view_sql = view_sql.replace("{mag_threshold}", str(mag_threshold)).replace(
        "{horizon_hours}", str(horizon_hours)
    )
    con.execute(view_sql)
    logger.info("✅ Vista 'ml_features_ready_view' creata/aggiornata con successo.")

def ingest_catalog_events(con: duckdb.DuckDBPyConnection, catalog_csv_path: Path) -> int:
    """Ingerisce il catalogo eventi INGV scaricato da download_cf_events.py.

    Il catalogo è il ground truth del Target_Allarme: senza di esso la vista
    ML genera solo target 0. L'ingest è idempotente (chiave event_id).
    """
    if not catalog_csv_path.exists():
        logger.warning(
            f"File catalogo eventi non trovato ({catalog_csv_path}): "
            "il Target_Allarme resterà 0. Scaricarlo con "
            "scripts/download_cf_events.py."
        )
        return 0
    df = pd.read_csv(catalog_csv_path)
    required = {"event_id", "time", "latitude", "longitude", "magnitude"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Catalogo eventi {catalog_csv_path} privo delle colonne {sorted(missing)}"
        )
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time", "magnitude"])
    # Registrazione del DataFrame come tabella temporanea (uso standard DuckDB)
    con.register("df_catalog", df)
    con.execute(
        """
        INSERT INTO catalog_events (
            event_id, time, latitude, longitude, depth_km,
            mag_type, magnitude, event_location_name, event_type
        )
        SELECT event_id, time, latitude, longitude, depth_km,
               mag_type, magnitude, event_location_name, event_type
        FROM df_catalog
        ON CONFLICT (event_id) DO UPDATE SET
            time = EXCLUDED.time,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            depth_km = EXCLUDED.depth_km,
            mag_type = EXCLUDED.mag_type,
            magnitude = EXCLUDED.magnitude,
            event_location_name = EXCLUDED.event_location_name,
            event_type = EXCLUDED.event_type;
        """
    )
    con.unregister("df_catalog")
    logger.info(f"Inseriti/aggiornati {len(df)} eventi del catalogo in catalog_events.")
    return len(df)


def ingest_waveform_energy(con: duckdb.DuckDBPyConnection, energy_csv_path: Path) -> int:
    """Ingerisce la statistica energetica oraria per stazione in DuckDB.

    Output di compute_mseed_deltas.py (CSV station_energy_hourly.csv): RMS e
    picco di ampiezza per ogni finestra oraria pre-processata. L'ingest e'
    idempotente sulla chiave (station_code, channel, hour).
    """
    if not energy_csv_path.exists():
        logger.warning(
            f"File energia oraria non trovato ({energy_csv_path}): le feature "
            "energetiche della vista ML resteranno a 0. Ricalcolarlo con "
            "scripts/compute_mseed_deltas.py."
        )
        return 0
    df = pd.read_csv(energy_csv_path)
    required = {"station", "window_iso", "rms", "peak"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV energia {energy_csv_path} privo delle colonne {sorted(missing)}"
        )
    df["window_iso"] = pd.to_datetime(df["window_iso"], errors="coerce")
    df = df.dropna(subset=["window_iso", "rms"])
    if df.empty:
        logger.warning(f"CSV energia {energy_csv_path} vuoto: nessuna riga da ingerire.")
        return 0
    df_insert = df[["station", "channel", "window_iso", "rms", "peak"]].rename(
        columns={"station": "station_code", "window_iso": "hour"}
    )
    con.register("df_energy", df_insert)
    con.execute(
        """
        INSERT INTO waveform_energy_hourly (station_code, channel, hour, rms, peak)
        SELECT station_code, channel, hour, rms, peak
        FROM df_energy
        ON CONFLICT (station_code, channel, hour) DO UPDATE SET
            rms = EXCLUDED.rms,
            peak = EXCLUDED.peak;
        """
    )
    con.unregister("df_energy")
    logger.info(f"Inserite/aggiornate {len(df_insert)} righe di energia oraria in waveform_energy_hourly.")
    return len(df_insert)


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
        # Compatibilità di schema: attach_coords_to_deltas.py produce le
        # coordinate metriche come x_m/y_m, lo schema del DB usa
        # easting/northing; network/elevation/location possono mancare.
        if 'easting' not in df_spatial.columns and 'x_m' in df_spatial.columns:
            df_spatial['easting'] = df_spatial['x_m']
        if 'northing' not in df_spatial.columns and 'y_m' in df_spatial.columns:
            df_spatial['northing'] = df_spatial['y_m']
        for _optional_col in ('network', 'elevation', 'location'):
            if _optional_col not in df_spatial.columns:
                df_spatial[_optional_col] = None
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
        # Stessa normalizzazione colonne di attach_coords: x_m/y_m -> easting/northing
        if 'easting' not in df_spatial_full.columns and 'x_m' in df_spatial_full.columns:
            df_spatial_full['easting'] = df_spatial_full['x_m']
        if 'northing' not in df_spatial_full.columns and 'y_m' in df_spatial_full.columns:
            df_spatial_full['northing'] = df_spatial_full['y_m']
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

    # Ingestione del catalogo eventi INGV (ground truth del target): cercato
    # in interim/ come output di download_cf_events.py, oppure nella root
    # della run per chi lo scarica manualmente.
    catalog_csv = run_dir / "interim" / "catalog_events.csv"
    if not catalog_csv.exists():
        catalog_csv = run_dir / "catalog_events.csv"
    ingest_catalog_events(con, catalog_csv)

    # Ingestione dell'energia oraria per stazione (feature RSAM-like)
    ingest_waveform_energy(con, run_dir / "interim" / "station_energy_hourly.csv")

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