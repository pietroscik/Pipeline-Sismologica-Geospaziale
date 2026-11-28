CHANGELOG
=======
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Target da catalogo eventi INGV (ground truth)**: `Target_Allarme = 1` se
  nelle prossime `horizon_hours` il catalogo contiene almeno un evento con
  `magnitude >= mag_threshold` entro 25 km. Tarato sul catalogo reale dei
  Campi Flegrei (950 eventi in 25 giorni, M 0.2-5.4): M>=3.5 con H=6h dà
  l'11.7% di ore positive. Sostituisce il proxy `max(delta_seconds) > 15.0`,
  degenere sui dati reali (banda 7.96-9.88 s senza segnale, 144 ore con
  target 0). Parametri ritarabili in `config.yaml` (sezione `catalog`)
- `scripts/download_cf_events.py`: downloader del catalogo con retry backoff
  (5/15/45s), 4xx permanenti senza retry, filtro geografico configurabile
- Feature esplicative dal catalogo: `eventi_catalogo_6h` e `magnitudo_max_6h`
  (solo finestre PASSATE, nessun data leakage)
- `run_pipeline.py`: flag passthrough `--download-channels/--download-workers/
  --download-stations-file/--download-block-days` e `--download-events`
  (catalogo per la finestra del download): il download non richiede più
  comandi separati dal notebook

### Fixed
- `compute_mseed_deltas.py`: il CSV di output crea le directory padre (run
  reale 2026-11-28: crash dopo 80s di elaborazione perché
  `runs/real_ingv/processed/` non esisteva)
- `run_pipeline.py`: con `--auto-ingest` su un database ancora inesistente
  l'ingest viene anticipata prima dell'analisi mobile (il training legge la
  vista ML dal DuckDB e falliva su runtime nuovi)
- Downloader FDSN: il client ObsPy cached il cui service discovery è fallito
  transitoriamente (rate-limit con worker paralleli) restava 'senza
  dataselect' per l'intera vita del thread, fallendo ogni task successivo
  (run reale 2026-11-27: 267/294 task failed). Ora il primo errore
  'does not have a dataselect service' invalida la cache e il retry rifà
  il discovery; se anche il client ricreato è senza dataselect, il
  fallimento è permanente e si passa al client successivo
- Test flaky del Transformer: aggiunto `torch.manual_seed` e allineato il
  range del check `best_threshold` alla griglia MCC [0.01, 0.95] (PR #41
  aveva esteso la griglia ma non l'assert del test)
- Downloader FDSN: il 204 "No data available" è ora trattato come esito
  permanente (`no_data`) senza retry né passaggio ai mirror — in precedenza
  ogni finestra vuota sprecava ~3-4 minuti di backoff inutili (run reale
  2026-11-27, 147 task HHZ)
- Downloader FDSN: l'errore "does not have a dataselect service" interrompe
  subito il client corrente senza retry (fallimento permanente del client)

### Changed
- `config.yaml`: rimossi i mirror IRIS/ORFEUS (URL senza servizio dataselect e
  senza dati della rete IV); il fallback manuale resta documentato nel config

## [0.2.0] - 2026-08-29

### Added
- Implemented complete mobile analysis pipeline with process_pipeline.py functionality
- Added command-line interface support to all mobile analysis scripts
- Added comprehensive environment variable documentation to README
- Added cryptography library to runtime dependencies in pyproject.toml

### Changed
- Refactored alert system to exclusively use standard environment variable prefixes (SMTP_*, EMAIL_*, WEBHOOK_*)
- Removed all ALERT_* prefix dependencies from mobile/alert_system.py _load_env_config() method
- Enhanced database configuration to support both DB_* and POSTGRES_* prefixes automatically
- Updated mobile analysis pipeline to properly chain all processing steps with correct file paths
- Standardized environment variable handling across all modules to align with .env.example and Docker configs

### Fixed
- Fixed empty process_pipeline.py file that was causing mobile analysis pipeline failures
- Fixed hardcoded file paths in associa_eventi.py and prepara_ml.py scripts
- Fixed environment variable mapping inconsistencies between code and deployment configurations
- Fixed module import issues and dependency resolution problems

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