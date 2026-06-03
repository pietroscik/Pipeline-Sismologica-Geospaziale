import pandas as pd
import json
import time

FILE_INPUT = "output_eventi_georeferenziati.csv.gz"
FILE_OUT_GEOJSON = "soli_epicentri_terremoti.geojson"

print("📖 Caricamento dati georeferenziati...")
tempo_inizio = time.time()
df = pd.read_csv(FILE_INPUT)

print("🧩 Calcolo degli epicentri geometrici per ogni evento...")
# Calcoliamo la latitudine e longitudine media di tutte le stazioni che hanno attivato lo stesso evento
# Questo ci dà una stima eccellente dell'epicentro reale del terremoto!
catalogo_epicentrici = df.groupby('event_id').agg(
    Lat_Epicentro=('latitude', 'mean'),
    Lon_Epicentro=('longitude', 'mean'),
    Stazioni_Attivate=('station', 'nunique'),
    Data_Ora=('arrival_iso', 'min')
).reset_index()

# Prendiamo i terremoti più significativi (es. avvertiti da almeno 5 stazioni) 
# per non sovraccaricare la mappa web di geojson.io
mappa_filtrata = catalogo_epicentrici[catalogo_epicentrici['Stazioni_Attivate'] >= 5]

print(f"🌍 Generazione del nuovo file GeoJSON con {len(mappa_filtrata):,} epicentri reali...")
features = []
for _, row in mappa_filtrata.iterrows():
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [row['Lon_Epicentro'], row['Lat_Epicentro']] # [Longitudine, Latitudine]
        },
        "properties": {
            "event_id": row['event_id'],
            "energia_stazioni": int(row['Stazioni_Attivate']),
           "data_ora": str(row['Data_Ora'])
        }
    }
    features.append(feature)

geojson_data = {
    "type": "FeatureCollection",
    "features": features
}

with open(FILE_OUT_GEOJSON, 'w') as f:
    json.dump(geojson_data, f, indent=2)

print(f"🎉 Fatto! Il file degli epicentri è pronto: '{FILE_OUT_GEOJSON}'")
print(f"⏱️ Elaborazione completata in {time.time() - tempo_inizio:.2f} secondi.")
