# Pipeline Sismologica Geospaziale

Questa repository contiene una pipeline completa e configurabile in Python per scaricare, processare e analizzare spazialmente i dati sismici (forme d'onda e residui di tempi di arrivo, o *delta*).
Sviluppato originariamente per il monitoraggio dei **Campi Flegrei**, il codice è stato interamente generalizzato: modificando il file `config.yaml` è possibile applicare le stesse analisi geospaziali a **qualsiasi area del mondo**, rete sismica o catalogo eventi.

Il progetto genera output standard (statistiche e mappe) compatibili con i principali software GIS.

## Requisiti di Sistema
Per eseguire correttamente tutti gli script è necessario un ambiente Python configurato con le seguenti librerie:
`obspy`, `pandas`, `geopandas`, `rasterio`, `scipy`, `matplotlib`, `pyproj`, `shapely`, `contextily`

## Flusso di Lavoro (Pipeline)

Gli script sono pensati per operare in sequenza. La pipeline si divide in 4 fasi principali:

### Fase 0: Selezione Spaziale (Opzionale)
*   **`scripts/select_stations_spatial.py`**
    Filtra il catalogo globale delle stazioni in base a un punto focale e un raggio (es. vulcano) o a un poligono personalizzato. 
    Genera un file di testo contenente solo le stazioni all'interno dell'area di interesse, ottimizzando i tempi di download ed elaborazione successivi.

### Fase 1: Acquisizione ed Esplorazione Forme d'Onda (FDSN)
*   **`scripts/download_waveforms.py`**
    Interroga i server FDSN per scaricare massivamente le forme d'onda in formato MiniSEED, basandosi sulle reti e stazioni definite in `config.yaml`. 
    Permette personalizzazioni avanzate via CLI (canali, intervalli di date). Utile la modalità `--dry-run` per ispezionare i file che verranno scaricati senza pesare sulla rete.
*   **`scripts/analyze_trace.py`**
    Utility indipendente per l'ispezione di un singolo file traccia. 
    Genera grafici della forma d'onda, esegue l'analisi di frequenza (FFT Spettro di ampiezza) e calcola i trigger tramite algoritmo STA/LTA (Short-Time/Long-Time Average). Restituisce un report JSON con le metriche calcolate sul segnale.

### Fase 2: Elaborazione dei Tempi di Arrivo (Delta)
*   **`scripts/prepare_science_deltas.py`**
    Carica i cataloghi degli eventi e dei relativi *picks* (le fasi P/S interpretate sulle forme d'onda). 
    Calcola per ciascuna traccia il "delta", ovvero lo scarto temporale dell'arrivo presso la stazione rispetto ad un riferimento temporale dell'evento (es. tempo mediano). Genera un CSV normalizzato.
*   **`scripts/compute_mseed_deltas.py`** *(Avanzato/Alternativo)*
    Alternativa diretta a `prepare_science_deltas.py`. Invece di usare i cataloghi, legge direttamente le forme d'onda MiniSEED e calcola i delta applicando algoritmi di triggering (es. STA/LTA) sul segnale grezzo.
*   **`scripts/compute_station_stats.py`**
    Prende in input il CSV generato in precedenza (ed eventualmente i dati di una run diversa come base per confronti) e aggrega i delta per ciascuna stazione. 
    Filtra eventuali stazioni con un conteggio di dati inferiore a una certa soglia e restituisce metriche statistiche come media, deviazione standard, e mediana.

### Fase 3: Spazializzazione del Dato Sismico
*   **`scripts/invert_station_locations.py`** *(Avanzato)*
    Script matematico sofisticato che ricalcola le coordinate ottimali (x, y) e le velocità per ogni stazione minimizzando i residui dei tempi di arrivo (metodo Least Squares). Ideale per validare o correggere le posizioni teoriche delle stazioni.
*   **`scripts/export_missing_stations.py`**
    Cross-controlla le stazioni presenti nei dataset dei delta e il catalogo note delle coordinate. Se una stazione è assente, si interfaccia automaticamente col server FDSN (es. rete `IV`) per estrapolare Latitudine, Longitudine e Quota e le salva in un file compensativo.
*   **`scripts/attach_coords_to_deltas.py`**
    Si occupa della georeferenziazione: unisce i valori statistici dei delta per stazione con le loro coordinate.
    L'operazione fondamentale qui è la **riproiezione spaziale**: converte le coordinate geografiche (WGS84 - EPSG:4326) in un sistema di coordinate metriche proiettate (per impostazione predefinita EPSG:32633 - UTM Zone 33N), fondamentale per non introdurre distorsioni in planimetria.

### Fase 4: Analisi Spaziale e Output GIS
*   **`scripts/analyze_delta_map.py`**
    Fase finale della pipeline dedicata alla reportistica e alla generazione di output GIS. Sfrutta il file arricchito di coordinate metriche e statistica per:
    *   Disegnare **scatter plot** spaziali delle posizioni.
    *   Costruire una **superficie interpolata** (metodo di interpolazione cubica di `scipy`) per visualizzare una mappa termica del campo continuo dei delta temporali, integrando delle isocline di contorno (contour).
    *   Identificare per cluster le aree con stazioni **anomale** (ritardi/anticipi marcati definendo una soglia via CLI).
    *   Esportare vettori in **Shapefile** e griglie raster in **GeoTIFF**, già formattati per essere inglobati nativamente in software come QGIS, ArcGIS, ecc.

## Orchestrazione ed Esecuzione

Il progetto supporta **esecuzioni isolate** tramite "Run Directory". Per evitare sovrascritture tra analisi diverse, l'orchestratore crea automaticamente una cartella dedicata sotto `runs/<NOME_RUN>/` (di default usa un timestamp) dove salva tutti i file intermedi e le mappe finali.

Puoi lanciare l'intera pipeline in automatico usando l'orchestratore Python o Bash:

```bash
# Esecuzione standard (genera una cartella con la data di oggi)
python run_pipeline.py

# Esecuzione con nome personalizzato
python run_pipeline.py --run-name flegrei_2023
```

In alternativa, ecco un esempio di esecuzione manuale dei singoli script:

```bash
# 0. Seleziona le stazioni nel raggio di 20km dalle coordinate 40.82, 14.14
python scripts/select_stations_spatial.py --point 40.82 14.14 20.0

# 1. Calcola le coordinate piane (UTM 33N)
python scripts/attach_coords_to_deltas.py --delta-csv runs/flegrei_2023/processed/station_stats.csv --stations-csv data/raw/stations.csv --output-csv runs/flegrei_2023/processed/deltas_spatial.csv --value-column base_mean

# 2. Avvia la produzione grafica e GIS
python scripts/analyze_delta_map.py --delta-csv runs/flegrei_2023/processed/deltas_spatial.csv --outdir runs/flegrei_2023/maps --export-geotiff --export-shapefile --anomaly-threshold 0.5
```
