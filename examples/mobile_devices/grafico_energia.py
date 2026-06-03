import pandas as pd
import numpy as np

# 1. Carica il catalogo pulito
print("📖 Caricamento catalogo...")
df = pd.read_csv("catalogo_terremoti_unici.csv")
df['Tempo_Riferimento_ISO'] = pd.to_datetime(df['Tempo_Riferimento_ISO'])

# Separazione delle due fasi
gennaio = df[df['Tempo_Riferimento_ISO'].dt.month == 1]
maggio = df[df['Tempo_Riferimento_ISO'].dt.month == 5]

# Calcolo delle frequenze strumentali (quante stazioni attivate)
freq_gennaio = gennaio['Numero_Stazioni_Attivate'].value_counts().sort_index()
freq_maggio = maggio['Numero_Stazioni_Attivate'].value_counts().sort_index()

print("📊 Generazione della matrice visiva testuale (ASCII Chart)...")
print("\n📈 FASE 2: DISTRIBUZIONE ENERGETICA A MAGGIO (Muro dei Mainshocks)")
print("-" * 65)
for stazioni, conteggio in sorted(freq_maggio.items(), reverse=True)[:10]:
    barre = "█" * int(stazioni)
    print(f"{stazioni:2d} Stazioni attivate | {conteggio:5d} eventi | {barre}")
print("-" * 65)

# 2. Salvataggio del report strutturato per QGIS/Excel
report_energia = df.groupby(['Numero_Stazioni_Attivate']).size().reset_index(name='Conteggio_Eventi')
report_energia.to_csv("distribuzione_energia_plot.csv", index=False)
print("✅ Dati pronti per il plot grafico: 'distribuzione_energia_plot.csv'")
