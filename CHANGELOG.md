CHANGELOG
=======
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed
- **Transformer: regolarizzazione esplicita dopo lo StandardScaler (PR #49)**.
  Lo scaling causale ha rimosso la regolarizzazione implicita dello scaling
  grezzo: su ~1.400 finestre di training la rete memorizzava il train set in
  <10 epoche (run reale 2026-11-28: train loss 1.09->0.24 mentre val loss
  1.66->5.67; Transformer scivolato a PR-AUC test 0.094, sotto il caso).
  Interventi: learning rate default 1e-3 -> **2e-4**, **AdamW** con
  weight_decay=1e-2, **dropout 0.3** su embedding e encoder layer. Il
  checkpoint resta su MCC val (NON su -val_loss: il criterio loss aveva
  selezionato i pesi dell'epoca 1 nel caso reale 2026-11-27, val loss
  34.5->64.7 sotto pos_weight mentre la classificazione migliorava). Verifica
  sintetica: divergenza val loss ridotta (0.93->1.09, era 1.66->5.67) e il
  checkpoint MCC generalizza sul test (MCC 0.71)

- **Scaling causale in `train_temporal_transformer`**: `StandardScaler`
  fittato SOLO sul sub-train (primi 85%, coda del 15% riservata alla
  validation; zero leakage). Senza scaling le feature a scala larghissima
  (energia RMS in counts) schiacciavano i logit del Transformer in una banda
  strettissima (run reale 2026-11-28: [0.477, 0.489]) vanificando il
  checkpoint su MCC. Il medesimo scaler finisce nel bundle per l'inferenza
- **Bundle `.pkl` auto-consistente**: `main()` salva model + scaler +
  feature_names + threshold + lookback in un unico artefatto joblib.
  Train-serve skew azzerato: l'inferenza applica ESATTAMENTE lo stesso
  preprocessing del training
- **`scripts/predict_risk.py`**: inferenza standardizzata dal bundle (Transformer
  con finestre 3D lookback, XGBoost Booster/sklearn, Random Forest), con
  `--evaluate` (matrice di confusione su Target_Allarme) e `--output-csv`
- **Vista `waveform_energy_robust_hourly`**: energia RSAM-like normalizzata
  per stazione sulla mediana delle 168 ore PRECEDENTI (base settimanale
  causale, `ROWS ... 1 PRECEDING`: mai il presente). Log-rapporto log10
  immune a gain strumento e deriva non stazionaria; soglia 0.301 (~2x)
  marca le stazioni a energia raddoppiata
- **Feature robuste in `ml_features_ready_view`**: rimosse le 5 colonne non
  stazionarie (`rms_medio`, `rms_max`, `picco_max`, `rms_max_ultime_6h/24h`)
  e aggiunte 8 colonne causali: `net_rms_log_ratio_med`,
  `net_peak_log_ratio_med`, `n_stations_energy`, `frac_stations_2x_rms` +
  i rolling `net_rms_log_ratio_6h`, `net_rms_log_ratio_max_24h`,
  `frac_stations_2x_6h`, `frac_stations_2x_max_24h`. Storico
  retrocompatibile via ALTER TABLE

- **Feature energetiche dal MiniSEED (RSAM-like)**: `compute_mseed_deltas`
  estrae ora, per ogni finestra oraria e stazione, RMS e picco di ampiezza
  della traccia pre-processata (stessa banda di detection). L'energia REALE
  del segnale era assente nei soli conteggi di trigger (proxy delta_seconds
  degenere: banda 7.96-9.88 s, Moran 0.17). Output in
  `interim/station_energy_hourly.csv` (flag `--energy-csv`), parallelizzato
  con `--workers` come i trigger. Dopo questa estrazione i MiniSEED raw
  diventano ridondanti per la pipeline
- Nuova tabella DuckDB `waveform_energy_hourly` (PK stazione+canale+ora,
  ingest idempotente) con funzione `ingest_waveform_energy()`
- 5 feature nella vista ML: `rms_medio`, `rms_max`, `picco_max` (istantanee
  orarie, aggregate sulla rete) e `rms_max_ultime_6h`, `rms_max_ultime_24h`
  (finestre PASSATE, no data leakage). Storico retrocompatibile: colonne
  ALTER aggiunte, NULL per le run pre-energia

- `compute_mseed_deltas`: elaborazione parallela per file con
  `ProcessPoolExecutor` e nuovo flag `--workers` (0 = auto: min(4, CPU));
  l'elaborazione era single-thread (run reale 2026-11-28 su Colab: 2,6 GB
  di RAM usati su 51 disponibili e timeout della Fase 2 superato su 2.400
  file)
- `run_pipeline`: flag `--mseed-workers` per inoltrare i processi paralleli
  alla Fase 2

### Fixed
- `run_pipeline`: timeout della Fase 2 proporzionale al volume dei
  MiniSEED (`max(timeout*2, 5s/file)`) invece del fisso `timeout*2` (600s,
  superato dopo 10 minuti sulla run reale a 3 mesi)
- `run_pipeline`: con `--auto-ingest` l'ingest viene ANTICIPATA SEMPRE prima
  del training, non solo a DB assente. Un DB stantio su Drive (run
  precedente) lasciava al training una vista ML obsoleta (caso reale
  2026-11-28: 144 ore vecchie vs 618 nuove, target tutto a 0)
- `train_risk_model`: selezione del best model ora deterministica e basata su
  average precision sul test (tie-break: MCC, poi transformer >
  random_forest > xgboost). Il vecchio criterio `test_f1_score` con `>`
  stretto, con pareggi a 0.0, selezionava sempre il primo modello addestrato
  (XGBoost, il peggiore: ROC-AUC 0.089 inverso)

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