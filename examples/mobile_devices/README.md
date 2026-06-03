# Legacy Mobile Devices Bundle

Questa cartella raccoglie il vecchio flusso esplorativo che in origine viveva in `Mobile Devices/`.
È stato integrato nel progetto come materiale di esempio e non sostituisce la pipeline principale.

## Contenuto utile

- `scoperte_automatiche.csv.gz`: delta gzip già pronti, utilizzabili dalla pipeline corrente come input di esempio
- `stations.csv`: anagrafica stazioni associata al dataset legacy
- `output_eventi_georeferenziati.csv.gz`, `catalogo_terremoti_unici.csv`, `dataset_ml_sismico.csv`: output storici per analisi successive
- `report_*.xlsx`, `*.geojson`, `*.csv`: artefatti generati dagli script di analisi

## Come usarlo

Gli script qui presenti sono auto-contenuti e usano nomi file relativi alla cartella corrente.
I CSV più grandi sono salvati in gzip e vengono letti direttamente da `pandas`.
Per eseguirli senza modifiche:

```bash
cd examples/mobile_devices
python analizza.py
python associa_eventi.py
python prepara_ml.py
```

## Integrazione nel progetto

Il dataset `scoperte_automatiche.csv.gz` è stato collegato all'orchestratore e all'interfaccia Streamlit del progetto:

```bash
python run_pipeline.py --run-name demo_legacy --start-phase 0 \
  --delta-csv examples/mobile_devices/scoperte_automatiche.csv.gz \
  --stations-csv examples/mobile_devices/stations.csv
```

In questo modo il progetto principale può essere provato subito, senza scaricare nuovi dati.
