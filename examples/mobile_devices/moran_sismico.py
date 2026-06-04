import pandas as pd
import numpy as np
import json
import time

print("📖 Caricamento degli epicentri unici...")
tempo_inizio = time.time()

# Carichiamo i dati degli epicentri dalla fase di maggio
df_geo = pd.read_csv("output_eventi_georeferenziati.csv.gz")
maggio_geo = df_geo[pd.to_datetime(df_geo['arrival_iso']).dt.month == 5]

# Aggregazione per calcolare coordinate e magnitudo (energia)
epicentri_maggio = maggio_geo.groupby('event_id').agg(
    lat=('latitude', 'mean'),
    lon=('longitude', 'mean'),
    energia=('station', 'nunique')
).reset_index().head(2500)

X = epicentri_maggio['lon'].values
Y = epicentri_maggio['lat'].values
Z = epicentri_maggio['energia'].values

n = len(Z)
print(f"📊 Calcolo dell'Indice di Moran su {n} eventi di Maggio...")

# Standardizzazione della variabile
z_mean = np.mean(Z)
z_std = Z - z_mean

# Creazione della matrice dei pesi W
W = np.zeros((n, n))
for i in range(n):
    # Distanza euclidea locale approssimata
    dist = np.sqrt((X - X[i])**2 + (Y - Y[i])**2)
    
    # TRUCCO GEOFISICO: Evitiamo la divisione per zero per eventi sovrapposti
    # Sostituiamo la distanza 0 con un valore spaziale microscopico
    dist[dist == 0] = 1e-6
    
    # Ignoriamo i warning per eventuali divisioni al limite
    with np.errstate(divide='ignore', invalid='ignore'):
        weights = 1.0 / dist
        
    weights[i] = 0.0              # Un punto non deve influenzare se stesso
    weights[dist > 0.015] = 0.0   # Taglio dell'influenza a circa 1.5 km di raggio
    
    # Standardizzazione per riga
    s = np.sum(weights)
    if s > 0:
        W[i, :] = weights / s
    else:
        W[i, :] = 0.0

print("🧮 Calcolo dei coefficienti Globali e Locali (LISA)...")

# Calcolo del Moran Globale corretto
spatial_lag = np.dot(W, z_std)
den = np.sum(z_std**2)
num = np.sum(z_std * spatial_lag)
moran_global = (num / den) if den != 0 else 0

# Calcolo LISA locale
lisa_local = (z_std / (den / n)) * spatial_lag if den != 0 else np.zeros_like(z_std)

# Classificazione Quadranti LISA
quadranti = []
for i in range(n):
    if z_std[i] > 0 and spatial_lag[i] > 0:
        quadranti.append("High-High (Hotspot)")
    elif z_std[i] < 0 and spatial_lag[i] < 0:
        quadranti.append("Low-Low (Coldspot)")
    elif z_std[i] > 0 and spatial_lag[i] < 0:
        quadranti.append("High-Low (Outlier)")
    else:
        quadranti.append("Low-High (Outlier)")

epicentri_maggio['LISA_Class'] = quadranti
epicentri_maggio['Local_Moran'] = lisa_local

print("\n📉 === RISULTATI DELL'ANALISI DI MORAN GLOBALE CORRETTA ===")
print("-" * 55)
print(f"🎯 INDICE DI MORAN GLOBALE (I): {moran_global:.4f}")
if moran_global > 0:
    print("➡️ Autocorrelazione Spaziale POSITIVA: Struttura a cluster confermata!")
    print("   La rottura del terreno sta seguendo chiare direttrici geometriche (faglie).")
else:
    print("➡️ Distribuzione casuale.")
print("-" * 55)

print("\n🗺️ === STATISTICHE LOCALI (LISA CAMPIONE) ===")
print(epicentri_maggio['LISA_Class'].value_counts().to_string())

# Esportazione del file GeoJSON per la mappatura
features = []
for _, row in epicentri_maggio.iterrows():
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [row['lon'], row['lat']]
        },
        "properties": {
            "event_id": row['event_id'],
            "energia": int(row['energia']),
            "lisa_cluster": row['LISA_Class'],
            "local_moran": float(row['Local_Moran'])
        }
    }
    features.append(feature)

geojson_data = {"type": "FeatureCollection", "features": features}
with open("analisi_lisa_epicentri.geojson", "w") as f:
    json.dump(geojson_data, f, indent=2)

print(f"\n🎉 File LISA salvato con successo: 'analisi_lisa_epicentri.geojson'")
print(f"⏱️ Elaborazione completata in {time.time() - tempo_inizio:.2f} secondi.")
