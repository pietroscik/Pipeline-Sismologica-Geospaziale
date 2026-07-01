import json
import os
import time

import pandas as pd

# Configurazione file (assumendo che siano tutti nella stessa cartella)
FILE_EVENTI = "scoperte_automatiche.csv.gz"
FILE_STAZIONI = "stations.csv"
OUTPUT_CSV = "output_eventi_georeferenziati.csv.gz"
OUTPUT_GEOJSON = "output_eventi_qgis.geojson"

print("🚀 Avvio pipeline di georeferenziazione sismica...")
tempo_inizio = time.time()

# 1. Caricamento dei Dataset
print("📖 Caricamento dei file in corso...")
df_eventi = pd.read_csv(FILE_EVENTI)
df_stations = pd.read_csv(FILE_STAZIONI)

# 2. Conversione dei Timestamp per ottimizzazione
if "arrival_iso" in df_eventi.columns:
    df_eventi["arrival_iso"] = pd.to_datetime(df_eventi["arrival_iso"], errors="coerce")

# 3. MERGE GEOSPAZIALE (Uniamo le coordinate alle stazioni)
print("🔗 Accoppiamento geometrico delle stazioni...")
# Uniamo i dati usando come chiavi sia il network che il codice stazione
df_merged = pd.merge(df_eventi, df_stations, on=["network", "station"], how="left")

# Verifichiamo se ci sono stazioni non mappate
mancanti = df_merged["latitude"].isna().sum()
if mancanti > 0:
    print(
        f"⚠️ Attenzione: {mancanti} righe non hanno trovato corrispondenza coordinate."
    )
else:
    print("✅ Ottimo! Tutte le stazioni sono state georeferenziate con successo.")

# 4. Filtro statistico del rumore (basato sui quantili di delta_seconds)
print("🧹 Pulizia statistica dei trigger tardivi o spurii...")
soglia_min = df_merged["delta_seconds"].quantile(0.02)
soglia_max = df_merged["delta_seconds"].quantile(0.98)
df_pulito = df_merged[
    (df_merged["delta_seconds"] >= soglia_min)
    & (df_merged["delta_seconds"] <= soglia_max)
]
print(f"📊 Dataset pulito: mantenute {len(df_pulito):,} righe su {len(df_merged):,}")

# Rimozione valori NaN sulle coordinate per evitare corruzione dello schema GeoJSON
df_pulito = df_pulito.dropna(subset=["latitude", "longitude"]).copy()

# 5. ESPORTAZIONE 1: CSV Enriched (Ideale per Excel o QGIS Delimited Text)
df_pulito.to_csv(OUTPUT_CSV, index=False)
print(f"💾 Esportato CSV arricchito: {OUTPUT_CSV}")

# 6. ESPORTAZIONE 2: Costruzione GEOJSON nativa (Senza bisogno di GeoPandas!)
print("🌍 Generazione del file GeoJSON strutturato per QGIS...")
features = []
# Analizziamo le prime 50.000 righe per il GeoJSON per non creare un file GIS troppo pesante sul tablet
for _, row in df_pulito.head(50000).iterrows():
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [row["longitude"], row["latitude"]],  # GIS vuole [Long, Lat]
        },
        "properties": {
            "event_id": row["event_id"],
            "station": row["station"],
            "channel": row["channel"],
            "delta_seconds": row["delta_seconds"],
            "arrival": str(row.get("arrival_iso", "N/A")),
            "elevation": row.get("elevation", 0.0),
        },
    }
    features.append(feature)

geojson_data = {"type": "FeatureCollection", "features": features}

with open(OUTPUT_GEOJSON, "w") as f:
    json.dump(geojson_data, f, indent=2)

tempo_fine = time.time() - tempo_inizio
print(f"🎉 Pipeline completata con successo in {tempo_fine:.2f} secondi!")
print(f"➡️ File GIS pronto per QGIS: '{OUTPUT_GEOJSON}'")
