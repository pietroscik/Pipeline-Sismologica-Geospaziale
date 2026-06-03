import obspy
from obspy.clients.fdsn import Client
from obspy.signal.trigger import classic_sta_lta, trigger_onset
import pandas as pd
import gc
import time

print("🚀 Avvio Pipeline Integrata (Download + Analisi) per i 5 giorni mancanti...")
start_time = time.time()

# 1. PARAMETRI DI CONFIGURAZIONE
NETWORK = "IV"       # Rete INGV
STATION = "*"        # Tutte le stazioni (puoi limitare a "MIDA,VAGA" se la RAM fatica)
CHANNEL = "HHZ"      # Canale verticale
STA_LEN = 1.0        # Finestra STA in secondi
LTA_LEN = 30.0       # Finestra LTA in secondi
THR_ON = 4.0         # Soglia attivazione trigger
THR_OFF = 1.5        # Soglia disattivazione trigger

# Date target (dal 30 maggio al 3 giugno 2026)
t_start = obspy.UTCDateTime("2026-05-30T00:00:00")
t_end = obspy.UTCDateTime("2026-06-03T23:59:59")

client = Client("INGV")
scoperte = []

print(f"📥 Connessione FDSN per il periodo: {t_start.date} -> {t_end.date}")

try:
    # 2. DOWNLOAD DELLE FORME D'ONDA
    print("⏳ Download delle tracce (potrebbe richiedere tempo a seconda della rete)...")
    st = client.get_waveforms(NETWORK, STATION, "*", CHANNEL, t_start, t_end)
    print(f"✅ Scaricate {len(st)} tracce. Avvio analisi STA/LTA...")

    # 3. ELABORAZIONE DEL SEGNALE (analyze_trace.py logic)
    for tr in st:
        stazione_id = tr.stats.station
        df_samp = tr.stats.sampling_rate
        
        # Pre-processing standard
        tr.detrend("linear")
        tr.taper(max_percentage=0.05)
        tr.filter("bandpass", freqmin=1.0, freqmax=15.0)
        
        # Calcolo STA/LTA
        cft = classic_sta_lta(tr.data, int(STA_LEN * df_samp), int(LTA_LEN * df_samp))
        triggers = trigger_onset(cft, THR_ON, THR_OFF)
        
        # Estrazione metadati per ogni trigger
        for trig in triggers:
            on_idx = trig[0]
            off_idx = trig[1]
            
            # Calcolo timestamp esatto
            trigger_time = tr.stats.starttime + (on_idx / df_samp)
            
            # Estrazione ampiezza di picco (PGA) nella finestra del trigger
            finestra_dati = tr.data[on_idx:off_idx]
            ampiezza_max = abs(finestra_dati).max() if len(finestra_dati) > 0 else 0
            
            # Salvataggio record
            scoperte.append({
                "stazione": stazione_id,
                "timestamp": trigger_time.datetime,
                "ampiezza": ampiezza_max,
                "durata": (off_idx - on_idx) / df_samp
            })
            
    # Liberiamo memoria
    del st
    gc.collect()

except Exception as e:
    print(f"❌ Errore durante l'elaborazione FDSN: {e}")

# 4. ESPORTAZIONE DEI RISULTATI
if scoperte:
    df_risultati = pd.DataFrame(scoperte)
    output_file = "scoperte_automatiche_5gg.csv.gz"
    df_risultati.to_csv(output_file, index=False)
    print(f"💾 Trovati {len(df_risultati)} eventi. Salvati in: {output_file}")
else:
    print("⚠️ Nessun trigger rilevato o errore nel download.")

elapsed = time.time() - start_time
print(f"🎉 Processo completato in {elapsed:.2f} secondi!")
