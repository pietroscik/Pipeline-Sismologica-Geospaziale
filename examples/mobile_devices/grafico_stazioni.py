import pandas as pd

FILE_INPUT = "output_eventi_georeferenziati.csv.gz"

print("📖 Generazione profilo di sensibilità stazioni dal dataset definitivo...")
df = pd.read_csv(FILE_INPUT)

# Calcoliamo le performance direttamente dal dataset arricchito
report_stazioni = df.groupby('station').size().reset_index(name='Conteggio_Rilevamenti')
top_15 = report_stazioni.sort_values(by='Conteggio_Rilevamenti', ascending=False).head(15)

print("\n🏆 CLASSIFICA STRUMENTALE DELLE STAZIONI (Campi Flegrei)")
print("-" * 65)
max_val = top_15['Conteggio_Rilevamenti'].max()

for idx, row in top_15.iterrows():
    # Creiamo una barra proporzionale al numero di rilevamenti per il terminale
    lunghezza_barra = int((row['Conteggio_Rilevamenti'] / max_val) * 30)
    barra_visiva = "■" * lunghezza_barra
    print(f"Stazione: {row['station']:4s} | Rilevamenti: {row['Conteggio_Rilevamenti']:6,d} | {barra_visiva}")
print("-" * 65)
print("📌 Le stazioni con la barra più lunga indicano la massima sensibilità epicentrale.")
