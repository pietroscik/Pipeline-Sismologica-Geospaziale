# Architettura dati e ingestione

## 1) Valutazione sintetica del progetto

Dall’analisi della struttura corrente emergono tre elementi chiave:

1. **Separazione storica incompleta** tra output consolidato e output di run:
   - `runs/` contiene esecuzioni puntuali (corretto).
   - `data/` contiene anche risultati di una prima run di test, con rischio di ambiguità.

2. **Discrezionalità operativa**:
   - naming run non completamente uniforme (`giugno29_02`, `ingestion_YYYYMMDD`, ecc.).
   - assenza di contratto dati unico per CSV intermedi/finali.

3. **Necessità ML temporale**:
   - le feature utili sono distribuite su file multipli;
   - manca una base canonica time-aware da cui costruire dataset coerenti.

## 2) Decisione architetturale

### Principio
- `runs/` = **artefatti di singola esecuzione** (riproducibilità operativa).
- `data/db/seismic_output.duckdb` = **fonte canonica per analytics/ML** (storico consolidato).

### Motivazione
DuckDB consente:
- append incrementale da CSV/Parquet;
- query veloci su serie temporali;
- zero gestione server;
- integrazione diretta con Python/Pandas/Geo stack.

## 3) Struttura cartelle (target)

```text
data/
  raw/                  # sorgenti originali, immutabili
  interim/              # staging tecnico
  processed/            # export curati
  db/
    seismic_output.duckdb   # database canonico

runs/
  <run_id>/
    selected_stations.txt
    interim/
    processed/
    maps/
    mobile_analysis/
    waveforms/
```

## 4) Regole operative (obbligatorie)

1. Ogni esecuzione deve avere un `run_id` univoco (`YYYYMMDD_HHMMSS` consigliato).
2. Nessun file in `runs/` è “fonte storica ufficiale” finché non viene ingestito nel DB.
3. Ogni tabella nel DB deve includere:
   - `run_id`
   - `reference_time` (o `reference_date`)
   - chiave stazione (`station_code`)
4. Gli export in `data/processed/` devono derivare da query/versioni DB tracciate.

## 5) Modello dati minimo nel DB

- `runs` (metadati run)
- `stations` (anagrafica stazioni)
- `waveform_inventory` (inventario file e finestra temporale)
- `deltas_spatial` (delta per stazione, tipo metrica, coordinate)
- `station_stats` (metriche aggregate)
- `anomalies` (eventi soglia e classificazione)

## 6) Contratti dati consigliati

### Chiavi
- `run_id`: stringa stabile
- `station_code`: codice stazione normalizzato
- `reference_time`: timestamp UTC

### Convenzioni
- timestamp ISO-8601 UTC
- nomi colonne snake_case
- unità esplicite (`delta_s`, `easting_m`, `northing_m`)
- nessuna sovrascrittura distruttiva: solo append + versionamento logico

## 7) Pipeline ingestione standard

1. Produzione artefatti in `runs/<run_id>/...`
2. Validazione schema CSV (colonne e tipi)
3. Ingestione nel DB (append transazionale)
4. Aggiornamento viste curate ML
5. Export opzionale verso `data/processed/`

## 8) Vista ML temporale (obiettivo)

Creare una vista/tavola `ml_features_timeseries` con:
- granularità giornaliera per stazione
- feature derivate (`delta`, statistiche mobili, flag anomalia)
- eventuale target/label separato

## 9) Rischi attuali e mitigazioni

- **Rischio**: inconsistenza tra run e dataset consolidato  
  **Mitigazione**: ingestione obbligatoria con controllo schema e log.

- **Rischio**: perdita di tracciabilità  
  **Mitigazione**: `run_id` + timestamp + checksum file origine.

- **Rischio**: bias temporale ML

## 10) Versioning & Audit

### Obiettivo
Garantire tracciabilità completa di ogni risultato: **input → codice → run → output DB**.

### Metadati minimi da registrare per ogni run
- `run_id` (univoco, formato consigliato `YYYYMMDD_HHMMSS`)
- `run_name`
- `run_timestamp_utc`
- `source_type` (es. `mseed`)
- `pipeline_version` (tag/commit Git)
- `config_hash` (SHA256 del file config usato)
- `notes`

### Audit sugli input
Per ogni file sorgente rilevante:
- `file_path`
- `file_size_bytes`
- `modified_time_utc`
- `sha256`

### Audit sugli output
Per ogni tabella caricata:
- `run_id`
- `table_name`
- `rows_inserted`
- `rows_deleted_pre_ingest` (per idempotenza)
- `ingest_started_utc`
- `ingest_finished_utc`
- `status` (`success` / `failed`)
- `error_message` (se presente)

### Regole operative
1. Nessuna ingestione senza `run_id`.
2. Ingestione in **transazione unica** con `ROLLBACK` su errore.
3. In caso di rerun: pulizia per `run_id` + reinserimento (idempotenza).
4. Le analisi ML devono usare solo dati consolidati nel DB.
5. Ogni anomalia di schema deve produrre warning esplicito a log.

### Tabelle tecniche consigliate
- `audit_ingestion_runs`
- `audit_ingestion_files`
- `audit_ingestion_tables`

### Verifiche periodiche
- conteggi per `run_id` coerenti tra tabelle figlie;
- assenza di `run_id` orfani;
- validità temporale (`reference_time` non nullo dove richiesto);
- completezza minima stazioni per run (soglia configurabile).