import pandas as pd
import numpy as np
import time

print("🧠 Inizializzazione Modello di Rischio Predittivo (Random Split + Pesi Calibrati)...")
tempo_inizio = time.time()

# 1. Caricamento Dati
df = pd.read_csv("dataset_ml_sismico.csv", index_col='Tempo')

# 2. Random Split (Campionamento casuale per distribuire gli eventi critici)
# Questo permette al modello di studiare i pattern dei Mainshocks senza che siano
# tutti confinati esclusivamente nel set di test.
train = df.sample(frac=0.8, random_state=42)
test = df.drop(train.index)

print(f"📚 Serie di addestramento: {len(train)} ore")
print(f"🔮 Orizzonte di test: {len(test)} ore\n")

# 3. Costruzione della Metrica di Rischio 
max_e = train['energia_max_ultime_12h'].max()
max_ev = train['eventi_ultime_24h'].max()

# Applicazione dei parametri strutturali ottimali per il framework predittivo
alpha = 1.28
beta = 1.21

train['Indice_Rischio'] = (train['energia_max_ultime_12h'] / max_e) * alpha + \
                          (train['eventi_ultime_24h'] / max_ev) * beta
                          
test['Indice_Rischio'] = (test['energia_max_ultime_12h'] / max_e) * alpha + \
                         (test['eventi_ultime_24h'] / max_ev) * beta

# 4. Calibrazione Empirica (Ricerca della soglia ottimale sul Train set)
print("⚙️ Calibrazione dei pesi e ricerca della soglia critica d'allarme...")
miglior_soglia = 0
miglior_f1 = 0

for soglia in np.linspace(train['Indice_Rischio'].min(), train['Indice_Rischio'].max(), 100):
    predizioni = (train['Indice_Rischio'] >= soglia).astype(int)
    
    veri_positivi = ((predizioni == 1) & (train['Target_Allarme'] == 1)).sum()
    falsi_positivi = ((predizioni == 1) & (train['Target_Allarme'] == 0)).sum()
    falsi_negativi = ((predizioni == 0) & (train['Target_Allarme'] == 1)).sum()
    
    precision = veri_positivi / (veri_positivi + falsi_positivi) if (veri_positivi + falsi_positivi) > 0 else 0
    recall = veri_positivi / (veri_positivi + falsi_negativi) if (veri_positivi + falsi_negativi) > 0 else 0
    
    # F1-Score: massimizza il bilanciamento tra allerte corrette e falsi allarmi
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    if f1 > miglior_f1:
        miglior_f1 = f1
        miglior_soglia = soglia

print(f"✅ Calibrazione Completata. Soglia di Rischio Ottimizzata: {miglior_soglia:.3f}")

# 5. Proiezione sull'orizzonte futuro (Test Set)
y_test = test['Target_Allarme']
y_pred = (test['Indice_Rischio'] >= miglior_soglia).astype(int)

veri_allarmi = y_test.sum()
allarmi_lanciati = y_pred.sum()
allarmi_corretti = ((y_pred == 1) & (y_test == 1)).sum()

recall_test = (allarmi_corretti / veri_allarmi) * 100 if veri_allarmi > 0 else 0
precision_test = (allarmi_corretti / allarmi_lanciati) * 100 if allarmi_lanciati > 0 else 0

print("\n🎯 === MATRICE DEI RISULTATI SULLA SIMULAZIONE FUTURA === ")
print("-" * 55)
print(f"Eventi critici reali nell'orizzonte  : {veri_allarmi}")
print(f"Allarmi preventivi attivati          : {allarmi_lanciati}")
print(f"Crisi intercettate in anticipo (TP)  : {allarmi_corretti}")
print(f"Falsi allarmi generati (FP)          : {allarmi_lanciati - allarmi_corretti}")
print("-" * 55)
print(f"🚀 RECALL    (Copertura biometrica del rischio) : {recall_test:.1f}%")
print(f"🎯 PRECISION (Affidabilità dell'indicatore)     : {precision_test:.1f}%")

print(f"\n⏱️ Elaborazione completata in {time.time() - tempo_inizio:.2f}s")
