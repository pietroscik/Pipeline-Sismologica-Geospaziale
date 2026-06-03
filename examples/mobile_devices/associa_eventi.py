import pandas as pd
import time

FILE_INPUT = "output_eventi_georeferenziati.csv.gz"

print("⏳ Caricamento del dataset georeferenziato e pulito...")
tempo_inizio = time.time()
df = pd.read_csv(FILE_INPUT)

print("🧩 Avvio associazione e clustering degli eventi sismici...")

# Raggruppiamo per ogni singolo evento unico (event_id)
# 1. Contiamo quante stazioni UNICHE hanno registrato l'evento
# 2. Estraiamo il tempo di riferimento centrale (event_reference_epoch)
# 3. Troviamo la stazione capofila (quella con il delta_seconds minore/più negativo)

catalogo_eventi = df.groupby('event_id').agg(
    Numero_Stazioni_Attivate=('station', 'nunique'),
    Canali_Coinvolti=('channel', 'count'),
    Tempo_Riferimento_Unix=('event_reference_epoch', 'first'),
    Tempo_Riferimento_ISO=('arrival_iso', 'min') # Il primo arrivo in assoluto
).reset_index()

# Ordiniamo gli eventi dal più energetico (più stazioni attivate) al più piccolo
catalogo_eventi = catalogo_eventi.sort_values(by='Numero_Stazioni_Attivate', ascending=False)

tempo_elaborazione = time.time() - tempo_inizio
print(f"✅ Elencati tutti gli eventi in {tempo_elaborazione:.2f} secondi!\n")

# --- STATISTICHE GENERALI ---
eventi_totali = len(catalogo_eventi)
top_evento = catalogo_eventi.iloc[0]

print("📊 === SINTESI FINALE DEL CATALOGO SISMICO ===")
print("-" * 50)
print(f"✨ Terremoti unici distinti identificati: {eventi_totali:,}")
print(f"📈 Media stazioni attivate per evento : {catalogo_eventi['Numero_Stazioni_Attivate'].mean():.1f}")
print("-" * 50)

print("\n🏆 Top 10 Terremoti più energetici dello sciame (più stazioni rilevanti):")
print(catalogo_eventi[['event_id', 'Numero_Stazioni_Attivate', 'Tempo_Riferimento_ISO']].head(10).to_string(index=False))
print("-" * 50)

# Salviamo il catalogo finale in un file CSV leggero e pronto per i grafici
FILE_OUT = "catalogo_terremoti_unici.csv"
catalogo_eventi.to_csv(FILE_OUT, index=False)
print(f"💾 Catalogo compresso salvato con successo: '{FILE_OUT}'")
