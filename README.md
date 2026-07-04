# Pipeline Sismologica Geospaziale

## Stato attuale (aggiornato)

- Ingestione in DuckDB attiva su `data/db/seismic_output.duckdb`.
- Script di ingestione run singola: `ingest_runs_to_db.py`.
- Script di ingestione batch di tutte le run: `scripts/ingest_all_runs.py`.
- Pipeline resa più robusta su differenze di schema CSV (`deltas_spatial.csv`).

## Comandi principali

### Ingestione singola run
```bash
python ingest_runs_to_db.py --run-id ingestion_20240516 --run-name "Analisi Pipeline: ingestion_20240516" --run-dir runs/ingestion_20240516 --source-type mseed
```

### Ingestione di tutte le run
```bash
python scripts/ingest_all_runs.py --runs-dir runs --source-type mseed
```

## Note operative

- L’ingestione è **idempotente per `run_id`**: in caso di rerun, i dati della stessa run vengono riallineati.
- Se `deltas_spatial.csv` non contiene `easting/northing`, i campi vengono inseriti a `NULL` (warning non bloccante).
- La vista `ml_features_ready_view` viene rigenerata a fine ingestione.

## Stato architetturale (aggiornato)

Il progetto adotta una separazione netta:

- `runs/`: esecuzioni singole e artefatti operativi.
- `data/db/seismic_output.duckdb`: base dati canonica per analisi storica e ML.
- `data/raw`, `data/interim`, `data/processed`: livelli dati standard.

Per dettagli operativi: `docs/ARCHITETTURA_DATI_E_INGESTIONE.md`.

## Linee guida rapide

1. Ogni run deve avere `run_id` univoco.
2. I risultati diventano ufficiali solo dopo ingestione nel DB.
3. Tutte le tabelle analitiche devono includere `run_id`, `station_code`, `reference_time`.
4. Il training ML deve usare dataset temporali derivati dal DB, non da file sparsi.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Tests](https://github.com/pietroscik/Pipeline-Sismologica-Geospaziale/actions/workflows/test.yml/badge.svg)](https://github.com/pietroscik/Pipeline-Sismologica-Geospaziale/actions/workflows/test.yml)

Questa repository contiene una pipeline in Python per acquisire, processare, versionare e analizzare dati sismologici, con particolare attenzione al contesto dei **Campi Flegrei**.

La struttura è stata generalizzata per lavorare con diverse aree geografiche e diverse reti sismiche, mantenendo un flusso univoco:

**`run_pipeline.py` → `mobile/monitor_campi_flegrei.py` → `data/` e `runs/` → `pages/alerts_dashboard.py`**

Il progetto produce output tabellari e cartografici compatibili con flussi GIS e con analisi operative su dashboard Streamlit.

## Indice

- [Architettura del Progetto](#architettura-del-progetto)
- [Requisiti di Sistema](#requisiti-di-sistema)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Flusso di Lavoro](#flusso-di-lavoro)
- [Analisi Mobile](#analisi-mobile)
- [Orchestrazione ed Esecuzione](#orchestrazione-ed-esecuzione)
- [Interfaccia Web (Streamlit)](#interfaccia-web-streamlit)
- [Struttura del Progetto](#struttura-del-progetto)
- [Dataset ed Esempi](#dataset-ed-esempi)
- [Obiettivi e Scope](#obiettivi-e-scope)
- [Data Contract](#data-contract)
- [Model Governance](#model-governance)
- [Qualità e Testing](#qualità-e-testing)
- [Runbook Operativo e Troubleshooting](#runbook-operativo-e-troubleshooting)
- [Sicurezza e Configurazione Sensibile](#sicurezza-e-configurazione-sensibile)
- [Sviluppo](#sviluppo)
- [Changelog](#changelog)
- [Licenza](#licenza)

## Architettura del Progetto

### Flusso logico

L'architettura del progetto è stata modernizzata per separare l'elaborazione dei dati dal loro consumo, con un database centrale come unica fonte di verità (Single Source of Truth).

1.  **Elaborazione Dati**: `run_pipeline.py` orchestra l'esecuzione di una singola analisi. Ogni `run` produce i suoi artefatti (CSV, mappe) in una cartella isolata in `runs/`.
2.  **Ingestione Dati**: Al termine di una `run`, lo script `ingest_runs_to_db.py` (attivato con `--auto-ingest`) consolida i risultati nel database centrale DuckDB (`data/db/seismic_output.duckdb`). Questo database aggrega i dati di tutte le esecuzioni.
3.  **Machine Learning & Analisi**:
    -   `train_risk_model.py` utilizza i dati storici aggregati in DuckDB per addestrare i modelli di rischio.
    -   `predict_from_db.py` usa i dati più recenti nel DB per generare predizioni con i modelli addestrati.
    -   L'interfaccia web `app.py` può sia avviare nuove pipeline sia visualizzare i dati storici e le analisi direttamente dal database.

```mermaid
graph TD
    subgraph "Input & Esecuzione"
        A(Web UI: app.py) --> B(Orchestratore: run_pipeline.py)
        C(CLI) --> B
    end

    subgraph "Flusso Dati"
        B -- "genera artefatti" --> D[Cartella di Run: runs/{nome_run}]
        D -- "--auto-ingest" --> E(Ingestione: ingest_runs_to_db.py)
        E -- "popola/aggiorna" --> F[<i class='fa fa-database'></i> DuckDB: seismic_output.duckdb]
    end

    subgraph "Consumo Dati & ML"
        F -- "legge dati per training" --> G(Training: train_risk_model.py)
        G -- "salva modello" --> H[models/best_model.pkl]
        
        F -- "legge dati per inferenza" --> I(Inferenza: predict_from_db.py)
        H -- "carica modello" --> I
        I -- "salva predizioni" --> J[runs/inference_results/]

        F -- "legge dati aggregati" --> A
    end
```

### Componenti principali

- `run_pipeline.py`: orchestrazione delle fasi
- `scripts/`: utility, analisi e generazione di mappe
- `mobile/`: training, monitoring, validazione, versioning
- `pages/alerts_dashboard.py`: visualizzazione Streamlit
- `data/`: input, intermedi e output canonici
- `runs/`: esecuzioni storiche e artefatti di run
- `mlruns/`: tracking MLflow

## Requisiti di Sistema

- Python 3.9 o superiore
- Windows 10/11 oppure ambiente Linux equivalente
- Git
- Ambiente virtuale Python
- Dipendenze scientifiche e geospaziali installate tramite `requirements.txt`

## Installazione

### Ambiente di sviluppo

```powershell
git clone https://github.com/pietroscik/Pipeline-Sismologica-Geospaziale.git
cd Pipeline-Sismologica-Geospaziale
python -m venv venv
.\venv\Scripts\activate
# Installa il progetto in modalità editabile con tutte le dipendenze di sviluppo e ML
pip install -e .[dev,ml]
```

### Verifica

```powershell
pytest
```

### Avvio dashboard

L'interfaccia web è il modo più semplice per interagire con la pipeline.

```powershell
streamlit run app.py
```

## Come Usare la Pipeline

Ci sono due modi principali per eseguire il progetto.

### 1. Tramite Interfaccia Web (Consigliato)

L'interfaccia grafica basata su Streamlit è il metodo più intuitivo. Permette di configurare tutti i parametri, avviare la pipeline e visualizzare i risultati senza usare la riga di comando.

```powershell
streamlit run app.py
```

### 2. Tramite Riga di Comando

Per automazione e scenari avanzati, puoi usare direttamente `run_pipeline.py`.

**Esempio 1: Esecuzione base con dati di default**
```powershell
# Esegue le fasi di default usando i dati presenti nella cartella /examples
python run_pipeline.py --run-name analisi_base --delta-csv examples/mobile_devices/scoperte_automatiche.csv.gz --stations-csv examples/mobile_devices/stations.csv
```

**Esempio 2: Download nuovi dati ed esecuzione completa**
```powershell
# Scarica i dati per un periodo specifico e poi esegue tutte le fasi di analisi
python run_pipeline.py --run-name download_dati_giugno --run-download --download-start 2026-06-01 --download-end 2026-06-10
```

**Esempio 3: Esecuzione con analisi mobile (Machine Learning)**
```powershell
# Esegue la pipeline standard e, al termine, avvia la sub-pipeline di analisi mobile per addestrare/usare modelli ML
python run_pipeline.py --run-name analisi_con_ml --mobile-analysis --mobile-min-stations 18
```

## Configurazione

### File principali

- `config.yaml`: configurazione generale della pipeline
- `.env`: variabili d’ambiente locali
- `.env.example`: template sicuro
- `mobile/config/*.yaml`: configurazioni del sistema mobile, monitor e alert

### Directory principali

- `data/`: input grezzi e stabili (es. catalogo stazioni principale).
- `runs/`: contiene gli output di ogni singola esecuzione, garantendo isolamento e riproducibilità.
- `mobile/models/`: modelli locali versionati.
- `mlruns/`: tracking degli esperimenti di ML con MLflow.

## Flusso di Lavoro

La pipeline è suddivisa in fasi sequenziali, ognuna eseguita da uno script specializzato.

*   **Fase 0: Selezione Spaziale**
    Filtra le stazioni sismiche all'interno di un'area geografica definita (es. un cerchio con raggio di 20km).
*   **Fase 1: Acquisizione Dati**
    Scarica le forme d'onda grezze (file MiniSEED) dai server FDSN (es. INGV) per le stazioni e il periodo temporale selezionati.
*   **Fase 2: Calcolo Delta e Statistiche**
    Processa i dati grezzi per calcolare i "delta temporali" (anticipi/ritardi) e aggrega le statistiche per ogni stazione.
*   **Fase 3: Georeferenziazione**
    Aggiunge le coordinate geografiche e metriche (UTM) ai dati elaborati, preparando il dataset per l'analisi spaziale.
*   **Fase 4: Creazione Mappe e Output GIS**
    Genera mappe di interpolazione, grafici e file standard GIS (GeoTIFF, Shapefile) per l'analisi in software come QGIS.

### Output

Gli output di ogni esecuzione vengono salvati in una cartella dedicata in `runs/{nome_run}/`. I file principali generati sono:

- `runs/{nome_run}/processed/deltas_spatial.csv`
- `runs/{nome_run}/processed/station_stats.csv`
- `runs/{nome_run}/maps/delta_interpolated.png`

## Analisi Mobile

Il modulo `mobile/` gestisce:

- validazione dati
- monitoring
- alerting
- training del modello di rischio
- versioning locale e tracking MLflow

### Componenti principali

- `mobile/data_validator.py`
- `mobile/alert_system.py`
- `mobile/monitor_campi_flegrei.py`
- `mobile/train_risk_model.py`
- `mobile/model_versioning.py`

## Interfaccia Web (Streamlit)

L’interfaccia Streamlit (`app.py`) consente di:

- Configurare ed eseguire la pipeline in modo visuale.
- Visualizzare i dati di monitoraggio e i risultati delle esecuzioni.
- Ispezionare le statistiche di stazione e le mappe generate.
- Esportare i risultati di un'intera esecuzione in un file ZIP.

## Struttura del Progetto

```text
Pipeline-Sismologica-Geospaziale/
├── app.py
├── config.yaml
├── data/
│   └── raw/
├── examples/
│   └── mobile_devices/
├── mobile/
│   ├── alert_system.py
│   ├── data_validator.py
│   ├── model_versioning.py
│   ├── monitor_campi_flegrei.py
│   └── train_risk_model.py
├── pages/
│   ├── alerts_dashboard.py
│   └── mobile_analysis_viewer.py
├── runs/
├── scripts/
├── tests/
├── mlruns/
├── run_pipeline.py
├── requirements.txt
└── README.md
```

## Dataset ed Esempi

La cartella `examples/mobile_devices/` contiene un dataset "legacy" utile per testare rapidamente la pipeline senza dover scaricare nuovi dati.

### `data/` vs `runs/`

- **`data/`**: Contiene dati di input "stabili" e condivisi, come il catalogo principale delle stazioni. È la "libreria" di base del progetto.
- **`runs/`**: Contiene gli output di ogni singola esecuzione. Ogni sottocartella è un "esperimento" archiviato, con i suoi dati, log e mappe, garantendo isolamento e riproducibilità.

## Obiettivi e Scope

### Obiettivi principali

- Automatizzare il processing sismologico su batch/runs.
- Produrre output tabellari e mappe geospaziali riproducibili.
- Supportare monitoraggio operativo con dashboard Streamlit.
- Versionare e tracciare i modelli di rischio.

### Fuori scope (attuale)

- Early warning real-time certificato.
- Sostituzione di sistemi ufficiali di Protezione Civile.

## Data Contract

### Input minimi
La pipeline può operare con diversi tipi di input, ma i più comuni sono un file di stazioni (`stations.csv`) e dei dati di eventi (`events.csv` e `picks.csv`) o un file di delta pre-calcolati.

### Output canonici (pipeline)
Ogni esecuzione produce una cartella in `runs/` contenente, tra gli altri:
- `runs/{nome_run}/processed/deltas_spatial.csv`
- `runs/{nome_run}/processed/station_stats.csv`
- `runs/{nome_run}/maps/*.png`

### Output monitor/alert

- `mobile/alerts/alerts_log.csv`
- `mobile/alerts/alerts_log.jsonl`

> Nota: la dashboard allarmi è pienamente operativa solo in presenza di un feed alert con campi temporali coerenti (`timestamp` o equivalente).

## Model Governance

### Versionamento

- Artefatti modello locali: `mobile/models/`
- Manifest registry: `models/registry/*.json`
- Tracking esperimenti: `mlruns/` (MLflow)

### Regole operative

- Ogni training produce metadata e metriche salvate.
- Il modello “corrente” deve essere identificabile in modo univoco.
- Il rollback è consentito selezionando una versione precedente valida.

## Qualità e Testing

### Suite test

- Unit/integration: cartella `tests/`
- Coverage HTML: `htmlcov/`

### Comandi

```powershell
pytest
pytest --maxfail=1 -q
```

## Runbook Operativo e Troubleshooting

### Esecuzione standard
Il modo più semplice per iniziare è lanciare l'interfaccia web.

```powershell
streamlit run app.py
```

### Problemi frequenti

1. **Dashboard vuota o timeline incoerente**
   - Verificare che sia stata completata almeno un'esecuzione e che la cartella `runs/{nome_run}` contenga dei file.
   - Verificare le colonne disponibili nei CSV caricati.
2. **Conflitti git dopo pull**
   - Risolvere file in stato `unmerged`, poi `git add` e `git commit`.

## Sicurezza e Configurazione Sensibile

- Non committare credenziali in chiaro.
- Usare `.env` locale e mantenere `.env.example` come template.
- Escludere artefatti sensibili/temporanei tramite `.gitignore`.

## Sviluppo

### Test

```powershell
pytest
```

### Formattazione

```powershell
black .
```

### Controllo statico

```powershell
flake8 .
```

## Changelog

Per il dettaglio delle modifiche vedere `CHANGELOG.md`.

## Licenza

Progetto distribuito sotto licenza MIT.
