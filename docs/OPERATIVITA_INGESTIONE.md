# Operatività ingestione (DuckDB)

## 1. Obiettivo

Consolidare i risultati delle cartelle `runs/<run_id>/` nel database canonico:
`data/db/seismic_output.duckdb`.

## 2. Flusso

1. Creazione/validazione schema DB.
2. Upsert metadati run.
3. Ingestione tabelle (`raw_deltas`, `station_stats`, `stations`, `deltas_spatial`).
4. Refresh vista `ml_features_ready_view`.

## 3. Idempotenza

La procedura elimina preventivamente i record legati al `run_id` nelle tabelle figlie prima del reinserimento, evitando duplicati su rerun.

## 4. Compatibilità schema CSV

`ingest_runs_to_db.py` gestisce alias di colonne per input non uniformi (es. `station/station_code`, `lat/latitude`, `lon/longitude`, `x/easting`, `y/northing`).

Se coordinate metriche non presenti:
- ingestione completata;
- `easting/northing = NULL`;
- warning a log.

## 5. Batch su tutte le run

Script: `scripts/ingest_all_runs.py`

Esempio:
```bash
python scripts/ingest_all_runs.py --runs-dir runs --source-type mseed
```

Opzione utile:
- `--stop-on-error`: interrompe al primo errore.

## 6. Troubleshooting rapido

### Errore `KeyError: ['easting', 'northing'] not in index`
Causa: colonne mancanti o con nome differente in `deltas_spatial.csv`.  
Stato: gestito con mappatura alias + fallback a `NULL`.

### Errore DuckDB su vista ML (`WHERE clause cannot contain aggregates`)
Causa: riferimento a colonna timestamp errata nella query vista.  
Stato: corretto usando `arrival_iso` e CTE per `max_arrival_hour`.

### Errore `SyntaxError: unterminated f-string literal`
Causa: stringa log non chiusa correttamente nel codice Python.  
Stato: corretto.

## 7. Verifiche post-ingestione

```sql
SELECT 'raw_deltas' t, count(*) n FROM raw_deltas WHERE run_id='ingestion_20240516'
UNION ALL
SELECT 'station_stats', count(*) FROM station_stats WHERE run_id='ingestion_20240516'
UNION ALL
SELECT 'deltas_spatial', count(*) FROM deltas_spatial WHERE run_id='ingestion_20240516';
```

```sql
SELECT count(*) AS righe, min(timestamp) AS min_ts, max(timestamp) AS max_ts
FROM ml_features_ready_view;
```