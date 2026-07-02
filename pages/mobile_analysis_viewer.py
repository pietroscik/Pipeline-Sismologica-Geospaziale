import json
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen, HeatMap
from streamlit_folium import st_folium

# Configurazione della pagina
st.set_page_config(
    page_title="Risultati Analisi Mobile", page_icon="📱", layout="wide"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"


@st.cache_data(show_spinner="Caricamento dati...")
def load_run_data(run_path: Path):
    """Carica tutti i dati necessari da una specifica cartella di analisi mobile."""
    data = {
        "summary": None,
        "models": [],
        "events": None,
        "alerts": None,
        "ml_dataset": None,
    }

    # 1. Riepilogo performance modelli
    summary_path = run_path / "models" / "classifica_modelli.csv"
    if summary_path.exists():
        data["summary"] = pd.read_csv(summary_path)

    # 2. Metadati dei modelli
    model_meta_files = list((run_path / "models").glob("*_meta.json"))
    for meta_file in model_meta_files:
        with open(meta_file, "r") as f:
            model_meta = json.load(f)
            model_name = meta_file.name.replace("_meta.json", "")
            data["models"].append({"name": model_name, "meta": model_meta})

    # 3. Catalogo eventi per la mappa
    events_path = run_path / "output" / "catalogo_terremoti_unici.csv"
    if events_path.exists():
        df_events = pd.read_csv(events_path)
        # Assicuriamoci che le colonne per le coordinate esistano e siano numeriche
        if "event_lat" in df_events.columns and "event_lon" in df_events.columns:
            df_events["event_lat"] = pd.to_numeric(
                df_events["event_lat"], errors="coerce"
            )
            df_events["event_lon"] = pd.to_numeric(
                df_events["event_lon"], errors="coerce"
            )
            data["events"] = df_events.dropna(subset=["event_lat", "event_lon"])

    # 4. Log allarmi
    alerts_path = run_path / "alerts" / "alerts_log.csv"
    if alerts_path.exists():
        data["alerts"] = pd.read_csv(alerts_path)

    # 5. Dataset ML
    ml_dataset_path = run_path / "output" / "dataset_ml_sismico.csv"
    if ml_dataset_path.exists():
        data["ml_dataset"] = pd.read_csv(ml_dataset_path)

    return data


def list_available_runs(runs_root: Path) -> list[str]:
    """Elenca le run che contengono un'analisi mobile completata."""
    if not runs_root.exists():
        return []
    valid_runs = []
    for p in runs_root.iterdir():
        if p.is_dir() and (p / "mobile_analysis").exists():
            valid_runs.append(p)
    # Ordina per data di modifica (le più recenti prima)
    valid_runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in valid_runs]

def get_color_from_value(value, vmin, vmax):
    """Mappa un valore a un colore da blu a rosso."""
    if pd.isna(value):
        return "#808080"  # Grigio per valori non disponibili
    # Normalizza il valore tra 0 e 1
    norm = (value - vmin) / (vmax - vmin) if (vmax - vmin) > 0 else 0.5
    if norm < 0.5:
        # Da blu a giallo
        r = int(255 * (norm * 2))
        g = int(255 * (norm * 2))
        b = int(255 * (1 - norm * 2))
    else:
        # Da giallo a rosso
        r = 255
        g = int(255 * (1 - (norm - 0.5) * 2))
        b = 0
    return f"#{r:02x}{g:02x}{b:02x}"

# --- Interfaccia Utente ---

st.title("📱 Visualizzatore Risultati Analisi Mobile")
st.markdown(
    "Questa pagina mostra i risultati dettagliati della pipeline di analisi mobile, inclusi le performance dei modelli, le mappe degli eventi e i log degli allarmi."
)

# Sidebar per la selezione della run
with st.sidebar:
    st.header("🔎 Selezione Esecuzione")
    available_runs = list_available_runs(RUNS_DIR)
    if not available_runs:
        st.warning("Nessuna esecuzione con analisi mobile trovata nella cartella `runs/`.")
        st.stop()

    selected_run = st.selectbox(
        "Scegli una run da analizzare:",
        options=available_runs,
    )

if not selected_run:
    st.info("Seleziona un'esecuzione dalla barra laterale per iniziare.")
    st.stop()

run_analysis_path = RUNS_DIR / selected_run / "mobile_analysis"

if not run_analysis_path.exists():
    st.error(
        f"La cartella 'mobile_analysis' non è stata trovata per la run '{selected_run}'."
    )
    st.stop()

st.header(f"Risultati per la Run: `{selected_run}`")

data = load_run_data(run_analysis_path)

tab1, tab2, tab3, tab4 = st.tabs(
    ["📊 Riepilogo e Modelli", "🗺️ Mappa Eventi", "🚨 Log Allarmi", "🗂️ Dataset"]
)

with tab1:
    st.subheader("Confronto Performance Modelli")
    if data["summary"] is not None:
        st.dataframe(data["summary"].set_index("Modello"), use_container_width=True)
    else:
        st.info("Nessun file di riepilogo ('classifica_modelli.csv') trovato.")

    st.subheader("Dettagli Metriche Modelli")
    if data["models"]:
        for model_info in data["models"]:
            with st.expander(f"**Modello: {model_info['name']}**"):
                st.json(model_info["meta"], expanded_keys=["params", "test_f1_score"])
    else:
        st.info("Nessun metadato dei modelli trovato (file *_meta.json).")

with tab2:
    st.subheader("Mappa Geografica degli Eventi Clusterizzati")
    if data["events"] is not None and not data["events"].empty:
        df_events = data["events"]

        # Controlla se la colonna per il colore esiste
        color_col = "station_count"
        if color_col not in df_events.columns:
            st.warning(f"Colonna '{color_col}' non trovata. I punti non saranno colorati.")
            color_col = None

        map_center = [df_events["event_lat"].mean(), df_events["event_lon"].mean()]
        m = folium.Map(location=map_center, zoom_start=10)
        Fullscreen().add_to(m)

        vmin, vmax = (None, None)
        if color_col:
            vmin = df_events[color_col].min()
            vmax = df_events[color_col].max()

        for _, row in df_events.iterrows():
            station_count = row.get(color_col)

            popup_html = f"""
            <b>Evento ID:</b> {row.get('event_id', 'N/A')}<br>
            <b>Timestamp:</b> {row.get('Tempo_Riferimento_ISO', 'N/A')}<br>
            <b>Stazioni:</b> {station_count or 'N/A'}<br>
            <b>Lat/Lon:</b> {row['event_lat']:.4f}, {row['event_lon']:.4f}
            """

            color = "gray"
            radius = 4
            if color_col and pd.notna(station_count):
                color = get_color_from_value(station_count, vmin, vmax)
                # Raggio dinamico in base al numero di stazioni
                radius = 3 + 7 * ((station_count - vmin) / (vmax - vmin) if (vmax - vmin) > 0 else 0.5)

            folium.CircleMarker(
                location=[row["event_lat"], row["event_lon"]],
                radius=radius,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(m)

        # Aggiungi una legenda alla mappa
        if color_col:
            legend_html = f"""
            <div style="position: fixed; bottom: 50px; left: 50px; width: 150px;
                        border:2px solid grey; z-index:9999; font-size:14px;
                        background-color:white; padding: 10px;">
            <b>Legenda</b><br>
            N. Stazioni<br>
            <i style="background: #ff0000;"></i>&nbsp; {int(vmax)} (max)<br>
            <i style="background: #ffff00;"></i>&nbsp; {int((vmin+vmax)/2)}<br>
            <i style="background: #0000ff;"></i>&nbsp; {int(vmin)} (min)<br>
            </div>
            """
            m.get_root().html.add_child(folium.Element(legend_html))

        st_folium(m, use_container_width=True, height=600)
    else:
        st.info("Nessun dato sugli eventi georeferenziati trovato.")

with tab3:
    st.subheader("Registro degli Allarmi Generati")
    if data["alerts"] is not None:
        st.dataframe(data["alerts"], use_container_width=True)
    else:
        st.info("Nessun log degli allarmi ('alerts_log.csv') trovato.")

with tab4:
    st.subheader("Dataset Utilizzati e Generati")
    if data["events"] is not None:
        with st.expander("Catalogo Terremoti Unici"):
            st.dataframe(data["events"], use_container_width=True)

    if data["ml_dataset"] is not None:
        with st.expander("Dataset per Machine Learning (dataset_ml_sismico.csv)"):
            st.dataframe(data["ml_dataset"], use_container_width=True)

    if data["events"] is None and data["ml_dataset"] is None:
        st.info("Nessun dataset trovato nella cartella 'output'.")