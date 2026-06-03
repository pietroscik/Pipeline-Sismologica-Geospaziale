import pandas as pd
import numpy as np

# Coordinate centro Pozzuoli (Rione Terra / CPOZ)
LAT_CENTRO = 40.8062
LON_CENTRO = 14.1410

print("📖 Caricamento epicentri per analisi di correlazione...")
# Carichiamo il catalogo dei terremoti unici
df = pd.read_csv("catalogo_terremoti_unici.csv")
df['Tempo_Riferimento_ISO'] = pd.to_datetime(df['Tempo_Riferimento_ISO'])

# Calcoliamo anche qui gli epicentri medi al volo per semplicità di calcolo delle distanze
df_eventi = pd.read_csv("output_eventi_georeferenziati.csv.gz")
epicentri = df_eventi.groupby('event_id').agg(
    lat=('latitude', 'mean'),
    lon=('longitude', 'mean'),
    mese=('arrival_iso', lambda x: pd.to_datetime(x).min().month)
).reset_index()

# Formula della distanza approssimata in km (Haversine o Pitagora locale)
def calcola_distanza(lat1, lon1, lat2, lon2):
    R = 6371.0 # Raggio terrestre in km
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    return R * c

epicentri['Distanza_dal_Centro_KM'] = calcola_distanza(LAT_CENTRO, LON_CENTRO, epicentri['lat'], epicentri['lon'])

# Confrontiamo le distanze medie tra Gennaio (Mese 1) e Maggio (Mese 5)
dist_gennaio = epicentri[epicentri['mese'] == 1]['Distanza_dal_Centro_KM']
dist_maggio = epicentri[epicentri['mese'] == 5]['Distanza_dal_Centro_KM']

print("\n📐 === ANALISI DI PROPAGAZIONE SPAZIALE ===")
print("-" * 55)
print(f"📍 Distanza media degli epicentri a Gennaio: {dist_gennaio.mean():.3f} km")
print(f"📍 Distanza media degli epicentri a Maggio : {dist_maggio.mean():.3f} km")
print("-" * 55)

if dist_maggio.mean() > dist_gennaio.mean():
    differenza = dist_maggio.mean() - dist_gennaio.mean()
    print(f"⚠️ PROPAGAZIONE IN CORSO: A maggio gli epicentri si sono espansi verso l'esterno di mediamente {differenza*1000:.1f} metri!")
else:
    print("🔒 CLUSTERING CONCENTRATO: Lo sciame è rimasto localizzato nello stesso volume focale.")
