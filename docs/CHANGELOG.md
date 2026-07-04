# Changelog

Tutte le modifiche rilevanti del progetto vengono tracciate in questo file.

## [2026-07-03]

### Added
- Script batch per ingestione massiva delle run: `scripts/ingest_all_runs.py`.
- Documentazione operativa ingestione: `docs/OPERATIVITA_INGESTIONE.md`.
- Formalizzazione architettura dati canonica con DuckDB (`data/db/seismic_output.duckdb`).

### Changed
- Ingestione resa **idempotente per `run_id`** in `ingest_runs_to_db.py`.
- Gestione robusta di input CSV con schema variabile (alias colonne: stazione, coordinate, metadati).
- Aggiornata la vista `ml_features_ready_view` con query compatibile DuckDB e uso coerente del campo temporale (`arrival_iso`).

### Fixed
- `KeyError` su colonne mancanti in `deltas_spatial.csv` (`easting`, `northing`, `location`) con fallback controllato.
- Errore DuckDB in creazione vista ML (`WHERE clause cannot contain aggregates`) correggendo la logica SQL.
- `SyntaxError: unterminated f-string literal` in logging ingestione.
- Stabilità della pipeline: fallimenti fase DB isolati e diagnosticabili via esecuzione diretta di `ingest_runs_to_db.py`.

### Notes
- Se `deltas_spatial.csv` non contiene coordinate metriche, i campi `easting/northing` vengono popolati a `NULL` (warning non bloccante).
- `runs/` resta archivio run-by-run; `data/db/` è la fonte canonica per analytics e ML temporale.