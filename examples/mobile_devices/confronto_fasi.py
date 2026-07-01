import pandas as pd

# Carica il catalogo compresso
df = pd.read_csv("catalogo_terremoti_unici.csv")
df["Tempo_Riferimento_ISO"] = pd.to_datetime(df["Tempo_Riferimento_ISO"])

# Separazione dei due blocchi di dati (Gennaio vs Maggio)
fase_gennaio = df[df["Tempo_Riferimento_ISO"].dt.month == 1]
fase_maggio = df[df["Tempo_Riferimento_ISO"].dt.month == 5]

# Calcolo dei giorni effettivi campionati
giorni_gennaio = fase_gennaio["Tempo_Riferimento_ISO"].dt.date.nunique()
giorni_maggio = fase_maggio["Tempo_Riferimento_ISO"].dt.date.nunique()

print("📊 === CONCONFRONTO QUANTITATIVO DELLE DUE FASI === ")
print("-" * 50)
print(f"🗓️ Fase 1 (Gennaio): {giorni_gennaio} giorni monitorati")
print(f"   ↳ Terremoti unici: {len(fase_gennaio):,}")
print(f"   ↳ Tasso di sismicità: {len(fase_gennaio)/giorni_gennaio:.1f} eventi/giorno")
print(
    f"   ↳ Media stazioni attivate: {fase_gennaio['Numero_Stazioni_Attivate'].mean():.2f}"
)
print("-" * 50)
print(f"🗓️ Fase 2 (20-29 Maggio): {giorni_maggio} giorni monitorati")
print(f"   ↳ Terremoti unici: {len(fase_maggio):,}")
print(f"   ↳ Tasso di sismicità: {len(fase_maggio)/giorni_maggio:.1f} eventi/giorno")
print(
    f"   ↳ Media stazioni attivate: {fase_maggio['Numero_Stazioni_Attivate'].mean():.2f}"
)
print("-" * 50)

# Vediamo dove si concentrano i 55 eventi maggiori (Mainshocks a 20 stazioni)
mainshocks_gennaio = fase_gennaio[fase_gennaio["Numero_Stazioni_Attivate"] == 20].shape[
    0
]
mainshocks_maggio = fase_maggio[fase_maggio["Numero_Stazioni_Attivate"] == 20].shape[0]

print(f"🏆 Localizzazione dei 55 eventi maggiori (a 20 stazioni):")
print(f"   ↳ Registrati a Gennaio: {mainshocks_gennaio}")
print(f"   ↳ Registrati a Maggio : {mainshocks_maggio}")
