import pandas as pd
import time

FILE_CSV = "scoperte_automatiche.csv.gz"
FILE_EXCEL_OUT = "report_sismico_aggregato.xlsx"

print("⏳ Caricamento del dataset massivo...")
df = pd.read_csv(FILE_CSV)

# Convertiamo la colonna arrival_iso in un vero formato Data/Ora di Pandas
print("📅 Conversione dei vettori temporali...")
df['arrival_iso'] = pd.to_datetime(df['arrival_iso'], errors='coerce')

print("📊 Elaborazione delle metriche quantitative...")

# --- 1. REPORT STAZIONI ---
# Calcoliamo quanti eventi ha rilevato ogni stazione e il delta medio/massimo
report_stazioni = df.groupby('station').agg(
    Conteggio_Rilevamenti=('event_id', 'count'),
    Delta_Secondo_Medio=('delta_seconds', 'mean'),
    Delta_Secondo_Max=('delta_seconds', 'max')
).reset_index().sort_values(by='Conteggio_Rilevamenti', ascending=False)

# --- 2. REPORT TEMPORALE (Conteggio per giorno) ---
# Estraiamo la data pulita (AAAA-MM-GG) per vedere il trend temporale
df['Giorno'] = df['arrival_iso'].dt.date
report_temporale = df.groupby('Giorno').size().reset_index(name='Numero_Eventi')

# --- 3. MATRICE CANALE / RETE (Tabella Pivot) ---
# Vediamo la distribuzione cross-tabulata dei canali sismici per stazione (Top 50 stazioni per leggibilità)
top_stazioni = report_stazioni['station'].head(50).tolist()
df_filtrato_top = df[df['station'].isin(top_stazioni)]
matrice_canali = pd.crosstab(df_filtrato_top['station'], df_filtrato_top['channel'])

print("💾 Scrittura del report multi-sheet in Excel...")
# Usiamo ExcelWriter per scrivere più fogli nello stesso file
with pd.ExcelWriter(FILE_EXCEL_OUT, engine='openpyxl') as writer:
    report_stazioni.to_excel(writer, sheet_name='Performance Stazioni', index=False)
    report_temporale.to_excel(writer, sheet_name='Trend Temporale', index=False)
    matrice_canali.to_excel(writer, sheet_name='Matrice Canali')

print(f"🎉 Analisi conclusa con successo! Generato: {FILE_EXCEL_OUT}")
