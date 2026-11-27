CHANGELOG
=======
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Added 85 new tests across the critical modules previously uncovered: `ingest_runs_to_db.py` (13), `run_pipeline.py` helper functions (23), `mobile/model_versioning.py` (18), `mobile/monitor_campi_flegrei.py` (22), `mobile/retrain_models.py` + `scripts/monthly_retrain_job.py` (30) — total suite now 181 tests (issue #4, #6, #7)
- Added `setup_monitoring_logger()` to `mobile/logging_config.py`
- Removed binary artifacts from git tracking: `mlruns/`, `mlflow.db`, `*.duckdb`, `*.joblib`, `*.skops` now gitignored; files remain on disk (`git rm --cached`, history untouched)

### Fixed
- Fixed broken `ml_features_ready_view` SQL in `ingest_runs_to_db.py`: DuckDB rejects aggregate subqueries inside `WHERE`; moved the `max(timestamp)` computation to a `data_boundary` CTE
- Fixed missing `shutil` import in `mobile/model_versioning.py` (flake8 F821 failing CI)
- Fixed `mobile/model_versioning.py` broken `current` symlink handling: `delete_version` checked the symlink after deleting its target (`exists()` is `False` for dangling symlinks, so the automatic rollback never ran) and `rollback`/`save_model` used `exists()` instead of `is_symlink()`, causing `FileExistsError` on replace
- Fixed `mobile/monitor_campi_flegrei.py` ImportError: the module referenced a non-existent API (`AlertConfig`, `AlertMessage`, `send_alert`) and a missing `setup_monitoring_logger`; now uses the real `AlertSystem` API (`trigger_alert`)
- Fixed `mobile/retrain_models.py` silent configuration failure: all `RetrainingConfig.get()` call sites passed defaults positionally (interpreted as keys), so every default silently returned `None`; the temporal trigger crashed into a bare `except` and the performance trigger used an undefined `self.models_dir`, never firing
- Fixed `mobile/retrain_models.py` imports of non-existent top-level modules (`model_version_manager`, `alert_system`); the `Fallback*` classes are now used directly, with a documented follow-up to integrate `mobile.model_versioning`
- Normalized `mobile/retrain_models.py` line endings to LF (mixed CR/CR-CR, violating `.editorconfig`)
- CI: Codecov upload failures (429 rate limit, no repository token) no longer fail the build

---

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