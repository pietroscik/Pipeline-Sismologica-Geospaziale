import io
import sys
import subprocess
import zipfile
from pathlib import Path
import pandas as pd
import folium
from folium.plugins import Fullscreen, HeatMap
from streamlit_folium import st_folium
import streamlit as st

# Configurazione base della pagina
st.set_page_config(page_title="Pipeline Sismologica", page_icon="🌋", layout="wide")

PROJECT_ROOT = Path(__file__).resolve().parent

@st.cache_data(show_spinner=False)
def load_spatial_data(csv_path: str, mtime: float) -> pd.DataFrame:
    """Carica il CSV in cache. L'argomento mtime assicura l'aggiornamento se il file cambia sul disco."""
    return pd.read_csv(csv_path)

st.title("🌋 Interfaccia Pipeline Sismologica Geospaziale")
st.markdown("Pannello di controllo web per eseguire analisi sismiche e visualizzare i risultati.")

# ==========================================
# SIDEBAR: CONFIGURAZIONE PARAMETRI
# ==========================================
with st.sidebar:
    st.header("⚙️ Configurazione")
    run_name = st.text_input("Nome Esecuzione (Run Name)", value="analisi_web_01")
    
    st.subheader("📍 Filtro Spaziale (Fase 0)")
    use_spatial_filter = st.checkbox("Applica Filtro Spaziale", value=True)
    if use_spatial_filter:
        lat = st.number_input("Latitudine (Punto Focale)", value=40.82, format="%.4f")
        lon = st.number_input("Longitudine (Punto Focale)", value=14.14, format="%.4f")
        radius = st.number_input("Raggio (km)", value=20.0, format="%.1f")
    
    st.markdown("---")
    run_button = st.button("🚀 Avvia Pipeline", type="primary", use_container_width=True)
    
    spatial_csv = PROJECT_ROOT / "runs" / run_name / "processed" / "deltas_spatial.csv"
    delta_range = None
    if spatial_csv.exists():
        st.markdown("---")
        st.header("🗺️ Filtri Mappa")
        df_temp = load_spatial_data(str(spatial_csv), spatial_csv.stat().st_mtime)
        if not df_temp.empty and "delta_seconds" in df_temp.columns:
            min_d = float(df_temp["delta_seconds"].min())
            max_d = float(df_temp["delta_seconds"].max())
            if min_d >= max_d:
                min_d, max_d = min_d - 1.0, max_d + 1.0
            delta_range = st.slider(
                "Filtra per Delta (secondi)",
                min_value=min_d, max_value=max_d, value=(min_d, max_d)
            )
        search_station = st.text_input("🔍 Cerca Stazione (es. CAAM)", help="Filtra per nome della stazione")

# ==========================================
# LOGICA DI ESECUZIONE
# ==========================================
if run_button:
    status_msg = st.empty()
    status_msg.info(f"Avvio della run: **{run_name}** in corso. Attendi il completamento...", icon="⏳")
    
    # 1. Esecuzione Fase 0 (Seleziona Stazioni)
    if use_spatial_filter:
        with st.spinner("Fase 0: Estrazione stazioni nell'area selezionata..."):
            cmd_fase0 = [
                sys.executable, str(PROJECT_ROOT / "scripts" / "select_stations_spatial.py"),
                "--point", str(lat), str(lon), str(radius),
                "--output-file", str(PROJECT_ROOT / "runs" / run_name / "selected_stations.txt")
            ]
            try:
                res0 = subprocess.run(cmd_fase0, capture_output=True, text=True, check=True)
                st.success("Selezione spaziale completata!")
            except subprocess.CalledProcessError as e:
                st.error("Errore durante la Fase 0 (Selezione Spaziale)")
                st.code(e.stderr)
                st.stop()
    
    # 2. Esecuzione Pipeline Principale (run_pipeline.py)
    with st.spinner("Esecuzione delle analisi spaziali (Fasi 1-4)..."):
        cmd_pipeline = [
            sys.executable, str(PROJECT_ROOT / "run_pipeline.py"),
            "--run-name", run_name
        ]
        try:
            res_pipe = subprocess.run(cmd_pipeline, capture_output=True, text=True, check=True)
            st.success("Pipeline completata con successo! 🎉")
            status_msg.empty() # Rimuove il banner azzurro di attesa liberando spazio
            with st.expander("Mostra Log Dettagliati dell'Orchestratore"):
                st.code(res_pipe.stdout)
        except subprocess.CalledProcessError as e:
            st.error("Errore critico durante l'esecuzione della pipeline!")
            st.code(e.stderr)
            st.stop()

# ==========================================
# VISUALIZZAZIONE RISULTATI
# ==========================================
st.header("🗺️ Mappa Interattiva delle Stazioni")
spatial_csv = PROJECT_ROOT / "runs" / run_name / "processed" / "deltas_spatial.csv"

if spatial_csv.exists():
    df_spatial = load_spatial_data(str(spatial_csv), spatial_csv.stat().st_mtime)
    # Verifichiamo che il CSV abbia le coordinate geografiche
    if not df_spatial.empty and "latitude" in df_spatial.columns and "longitude" in df_spatial.columns:
        
        # Applica il filtro del cursore
        if delta_range is not None:
            df_spatial = df_spatial[
                (df_spatial["delta_seconds"] >= delta_range[0]) & 
                (df_spatial["delta_seconds"] <= delta_range[1])
            ]
            
        # Applica il filtro di ricerca testuale
        if search_station:
            df_spatial = df_spatial[df_spatial["station"].str.contains(search_station.upper(), na=False)]
            
        # Centriamo la mappa automaticamente calcolando la media delle coordinate
        center_lat = df_spatial["latitude"].mean() if not df_spatial.empty else 40.82
        center_lon = df_spatial["longitude"].mean() if not df_spatial.empty else 14.14
        
        # Inizializziamo la mappa vuota
        m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles=None)
        
        # Aggiunta basemap multiple
        folium.TileLayer('CartoDB positron', name='CartoDB Light (Default)').add_to(m)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Esri Satellite'
        ).add_to(m)
        folium.TileLayer('OpenStreetMap', name='OpenStreetMap').add_to(m)
        
        Fullscreen(position='topright').add_to(m)
        stazioni_group = folium.FeatureGroup(name="Stazioni Sismiche").add_to(m)

        # Creazione del layer HeatMap per i ritardi (delta > 0)
        df_delays = df_spatial[df_spatial["delta_seconds"] > 0]
        if not df_delays.empty:
            heat_data = df_delays[['latitude', 'longitude', 'delta_seconds']].values.tolist()
            heat_map_group = folium.FeatureGroup(name="HeatMap Ritardi", show=False).add_to(m)
            HeatMap(
                heat_data,
                radius=25, blur=15,
                name="HeatMap Ritardi"
            ).add_to(heat_map_group)

        # Aggiungiamo un marker per ogni stazione
        for _, row in df_spatial.iterrows():
            delta = row.get("delta_seconds", 0)
            # Colore indicativo: rosso se ritardo marcato (>0.1), blu se anticipo marcato (<-0.1)
            color = "crimson" if delta > 0.1 else "darkblue" if delta < -0.1 else "gray"
            
            popup_html = f"<b>Stazione: {row['station']}</b><br>Delta: {delta:.3f} s"
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=7,
                popup=folium.Popup(popup_html, max_width=200),
                color=color, fill=True, fill_color=color, fill_opacity=0.7
            ).add_to(stazioni_group)
            
        folium.LayerControl(position='topright').add_to(m)
        
        # Renderizziamo la mappa dentro Streamlit occupando tutta la larghezza
        st_folium(m, use_container_width=True, height=500, returned_objects=[])

        # Tabella interattiva dei dati filtrati
        st.markdown("### 📋 Dati Stazioni Filtrati")
        if not df_spatial.empty:
            st.dataframe(
                df_spatial.sort_values("delta_seconds", ascending=False),
                use_container_width=True,
                hide_index=True
            )
            st.caption(f"Stazioni visualizzate: {len(df_spatial)}")
        else:
            st.warning("Nessuna stazione corrisponde ai criteri di filtro impostati.")

st.header("📊 Mappe e Grafici Generati")
maps_dir = PROJECT_ROOT / "runs" / run_name / "maps"

if maps_dir.exists():
    images = list(maps_dir.glob("*.png"))
    if images:
        cols = st.columns(2)  # Crea un layout a due colonne
        for i, img_path in enumerate(images):
            with cols[i % 2]:
                st.image(str(img_path), caption=img_path.name, use_container_width=True)

# ==========================================
# ESPORTAZIONE RISULTATI
# ==========================================
run_folder = PROJECT_ROOT / "runs" / run_name
if run_folder.exists() and any(run_folder.iterdir()):
    st.markdown("---")
    st.header("💾 Esporta Risultati")
    st.write("Scarica l'intera cartella dell'esecuzione (inclusi file CSV, Shapefile, GeoTIFF e immagini grafiche).")
    
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in run_folder.rglob("*"):
            if file_path.is_file():
                zip_file.write(file_path, file_path.relative_to(run_folder))
    
    st.download_button(
        label="📦 Scarica intera Run (ZIP)",
        data=buffer.getvalue(),
        file_name=f"{run_name}_results.zip",
        mime="application/zip",
        type="primary"
    )