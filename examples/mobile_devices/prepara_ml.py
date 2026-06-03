import pandas as pd
import numpy as np
import time

print("🚀 Avvio Feature Engineering per il Machine Learning...")
tempo_inizio = time.time()

# 1. Caricamento del catalogo pulito
df = pd.read_csv("catalogo_terremoti_unici.csv")
df['Tempo'] = pd.to_datetime(df['Tempo_Riferimento_ISO'])
df.set_index('Tempo', inplace=True)

# 2. Resampling: Raggruppiamo i dati ora per ora
print("🧩 Raggruppamento della sismicità in finestre orarie...")
df_orario = df.resample('1h').agg(
    numero_eventi=('event_id', 'count'),
    energia_max=('Numero_Stazioni_Attivate', 'max'),
    energia_media=('Numero_Stazioni_Attivate', 'mean')
).fillna(0)

# 3. Creazione della Variabile TARGET (Y): 
# Ci chiediamo: "Nelle prossime 24 ore, ci sarà un evento che attiverà almeno 18 stazioni?"
# Usiamo shift(-24) per guardare nel futuro.
print("🎯 Generazione della variabile Target (Previsione a 24h)...")
df_orario['max_energia_futura_24h'] = df_orario['energia_max'].rolling(window=24, min_periods=1).max().shift(-24)
df_orario['Target_Allarme'] = (df_orario['max_energia_futura_24h'] >= 18).astype(int)

# 4. Creazione delle FEATURES (X) per l'addestramento:
# Il modello deve imparare dai trend del passato recente
print("⚙️ Calcolo dei pattern sismici storici (Rolling Features)...")
df_orario['eventi_ultime_6h'] = df_orario['numero_eventi'].rolling(window=6).sum()
df_orario['eventi_ultime_24h'] = df_orario['numero_eventi'].rolling(window=24).sum()
df_orario['energia_max_ultime_12h'] = df_orario['energia_max'].rolling(window=12).max()
df_orario['trend_energia_media'] = df_orario['energia_media'].rolling(window=12).mean()

# Rimuoviamo le righe con dati mancanti creati dal rolling e dallo shift
df_ml = df_orario.dropna().copy()

print("\n📊 === SINTESI DATASET MACHINE LEARNING ===")
print("-" * 55)
print(f"Righe totali (Ore campionate): {len(df_ml)}")
print(f"Ore con Allarme Mainshock (Target=1): {df_ml['Target_Allarme'].sum()}")
print("-" * 55)

# Salvataggio del dataset pronto per il modello
FILE_OUT = "dataset_ml_sismico.csv"
df_ml.to_csv(FILE_OUT)
print(f"\n✅ Dataset addestramento salvato: '{FILE_OUT}'")
print(f"⏱️ Tempo impiegato: {time.time() - tempo_inizio:.2f}s")
