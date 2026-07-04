CHANGELOG
=======
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2024-07-04

### Added
- **Database-centric Architecture**: Introdotto un database DuckDB (`data/db/seismic_output.duckdb`) come fonte di verità canonica per analisi e ML.
- **Data Ingestion**: Creato lo script `ingest_runs_to_db.py` per un'ingestione idempotente e transazionale dei risultati delle pipeline run nel database.
- **Unified ML Workflow**: Sviluppato un nuovo script di training (`scripts/train_risk_model.py`) che carica i dati direttamente dal database, garantendo coerenza.
- **SQL-based Feature Engineering**: Implementata una vista SQL (`ml_features_ready_view`) in DuckDB per centralizzare e standardizzare la creazione di feature per il machine learning.
- **Automatic Ingestion**: Aggiunto il flag `--auto-ingest` a `run_pipeline.py` per automatizzare il salvataggio dei risultati nel database al termine di un'esecuzione.
- **Architectural Documentation**: Creato il file `docs/ARCHITETTURA_DATI_E_INGESTIONE.md` che descrive la nuova architettura dati.

### Changed
- **Major Refactoring**: La pipeline è stata riprogettata per passare da un approccio basato su file a un'architettura incentrata sul database, migliorando la coerenza e la scalabilità.
- **Dependency Management**: Abbandonati i file `requirements.txt` in favore di un moderno `pyproject.toml` (PEP 621) con gestione di dipendenze opzionali per sviluppo (`dev`) e machine learning (`ml`).
- **Orchestration (`run_pipeline.py`)**: Lo script principale è stato reso più robusto con una migliore gestione delle fasi, degli errori e un'integrazione nativa con il training ML e l'ingestione nel DB.
- **Continuous Integration (CI)**: Il workflow di GitHub Actions (`test.yml`) è stato potenziato per includere controlli di formattazione (`black`), stile (`flake8`) e per eseguire i test (`pytest`) con una configurazione di coverage corretta e caching delle dipendenze.
- **Developer Experience**: Aggiunti file standard come `CONTRIBUTING.md` e migliorati i template per Pull Request per facilitare la collaborazione.

### Removed
- **Legacy ML Scripts**: Rimosso lo script di training obsoleto (`examples/mobile_devices/train_modello.py`) e lo script di creazione feature (`create_ml_dataset.py`), sostituiti dal nuovo workflow basato su database.
- **Redundant Dependency Files**: Eliminati i file `requirements.txt`.

---

## [0.1.1] - 2026-07-01

### Fixed
- Fixed `scripts/download_cf_waveforms.py` FDSN client discovery logic and URL normalization.
- Added support for `--clients` fallback list and ensured `--client`/config precedence is handled correctly.
- Defaulted `--block-days` to `1` to avoid large single requests rejected by INGV FDSN (`413`).
- Sanitized Windows filenames for wildcard channels such as `HN?` when writing MiniSEED output.
- Fixed syntax issues in `mobile/api/crud.py` and `scripts/analyze_trace.py` that prevented formatting with Black.
- Updated README and linting docs to exclude `venv` from `flake8` and document the correct FDSN download usage.

---

## [0.1.0] - 2026-05-29

### Added
- Added MIT license
- Added setup.py for package installation
- Added pyproject.toml (PEP 621 standard)
- Added GitHub Actions workflow for CI/CD
- Added tests/ directory with example test
- Added .pre-commit-config.yaml for automatic linting
- Updated requirements.txt with missing dependencies (shapely, contextily)
- Updated README.md with badges and Installation/Test sections

### Changed
- Improved project structure for professional standards

---

## [0.0.1] - 2026-05-29

### Added
- Initial version of the pipeline
- Scripts for geospatial seismic analysis (4 phases)
- Orchestration via Python and Bash
- Configuration via config.yaml
- Complete documentation in README.md
