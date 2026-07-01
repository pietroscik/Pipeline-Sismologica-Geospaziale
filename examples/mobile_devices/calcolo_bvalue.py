import numpy as np
import pandas as pd

FILE_CATALOGO = "catalogo_terremoti_unici.csv"

print("📖 Caricamento del catalogo per l'analisi Gutenberg-Richter...")
df = pd.read_csv(FILE_CATALOGO)
df["Tempo_Riferimento_ISO"] = pd.to_datetime(df["Tempo_Riferimento_ISO"])

# Separiamo le due fasi
gennaio = df[df["Tempo_Riferimento_ISO"].dt.month == 1]
maggio = df[df["Tempo_Riferimento_ISO"].dt.month == 5]


def calcola_b_value(data, nome_fase):
    # 1. Calcoliamo la frequenza di ogni "magnitudo" (numero di stazioni)
    conteggi = data["Numero_Stazioni_Attivate"].value_counts().sort_index()
    m_val = conteggi.index.values
    freq = conteggi.values

    # 2. Calcoliamo la frequenza cumulativa (Quanti eventi hanno energia >= M)
    cum_freq = np.array([np.sum(freq[i:]) for i in range(len(freq))])

    # 3. Trasformazione logaritmica (Base 10) come richiede la formula Log10(N) = a - bM
    log_cum_freq = np.log10(cum_freq)

    # 4. Regressione Lineare (Metodo dei minimi quadrati)
    # Tagliamo la "coda" di completezza del catalogo (es. usiamo i dati da 6 a 15 stazioni)
    # per avere la pendenza reale della curva
    filtro_validi = (m_val >= 6) & (m_val <= 15) & (cum_freq > 0)
    X = m_val[filtro_validi]
    Y = log_cum_freq[filtro_validi]

    if len(X) > 1:
        slope, intercept = np.polyfit(X, Y, 1)
        b_value = -slope  # Il b-value è la pendenza invertita di segno

        print(f"\n📈 FASE: {nome_fase}")
        print("-" * 40)
        print(f"   ↳ b-value stimato (Proxy M): {b_value:.4f}")
        print(f"   ↳ Coefficiente 'a' (Attività): {intercept:.4f}")
        return b_value
    else:
        print(f"Dati insufficienti per regressione in {nome_fase}")
        return None


print("\n🧮 CALCOLO DELLE PENDENZE DI RILASCIO ENERGETICO")
b_gen = calcola_b_value(gennaio, "Fase 1 (Gennaio)")
b_mag = calcola_b_value(maggio, "Fase 2 (20-29 Maggio)")

print("\n⚖️ === DIAGNOSI GEOFISICA FINALE ===")
if b_mag is not None and b_gen is not None:
    variazione = ((b_mag - b_gen) / b_gen) * 100
    print(f"Variazione percentuale del b-value: {variazione:+.1f}%")

    if variazione > 5.0:
        print("⚠️ RISPOSTA FLUIDODINAMICA: Il b-value è aumentato nettamente a maggio.")
        print("   Significa che la crisi attuale è guidata da una forte iniezione di")
        print(
            "   fluidi magmatici o gas in pressione che stanno sbriciolando la roccia."
        )
    elif variazione < -5.0:
        print("⚠️ RISPOSTA TETTONICA: Il b-value è crollato a maggio.")
        print(
            "   Il sistema sta accumulando stress su asperità rigide (faglia sottomarina),"
        )
        print("   aumentando la probabilità di un evento singolo molto più forte.")
    else:
        print(
            "🔒 SISTEMA STABILE: La meccanica di fratturazione non è cambiata tra i due mesi."
        )
