import pandas as pd

# 1. Carica il dataset arricchito che hai appena generato
print("📖 Caricamento del dataset georeferenziato...")
df = pd.read_csv("output_eventi_georeferenziati.csv.gz")

# 2. Converti la colonna reale (arrival_iso) in formato datetime
# Usiamo errors='coerce' per evitare blocchi in caso di stringhe corrotte
df['arrival_iso'] = pd.to_datetime(df['arrival_iso'], errors='coerce')

# 3. Estrai l'ora del giorno (da 0 a 23)
df['ora'] = df['arrival_iso'].dt.hour

# 4. Conta i trigger per ogni ora del giorno e ordina l'indice da 0 a 23
distribuzione_oraria = df['ora'].value_counts().sort_index()

print("\n📊 Distribuzione oraria dei trigger (Analisi Rumore Antropico):")
print("-" * 50)
for ora, conteggio in distribuzione_oraria.items():
    print(f"Ora {ora:02d}:00  -->  {conteggio:,} trigger")
print("-" * 50)

# 5. Calcola una metrica veloce di controllo (Rapporto Giorno/Notte)
# Consideriamo Notte (00-06) e Giorno (08-16)
trigger_notte = df[df['ora'].isin([0, 1, 2, 3, 4, 5])].shape[0]
trigger_giorno = df[df['ora'].isin([8, 9, 10, 11, 12, 13, 14, 15, 16])].shape[0]

print(f"\n📉 Trigger totali in fascia notturna (00-06): {trigger_notte:,}")
print(f"📈 Trigger totali in fascia diurna (08-16): {trigger_giorno:,}")
