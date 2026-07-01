CHANGELOG
=======
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
