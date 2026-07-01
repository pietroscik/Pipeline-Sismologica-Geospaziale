import io
import subprocess
import sys
import zipfile
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen, HeatMap
from streamlit_folium import st_folium

# Configurazione base della pagina

st.set_page_config(page_title="Pipeline Sismologica", page_icon="🌋", layout="wide")


PROJECT_ROOT = Path(__file__).resolve().parent

EXAMPLE_LEGACY_DIR = PROJECT_ROOT / "examples" / "mobile_devices"


@st.cache_data(show_spinner=False)
def load_spatial_data(csv_path: str, mtime: float) -> pd.DataFrame:
    """Carica il CSV in cache. L'argomento mtime assicura l'aggiornamento se il file cambia sul disco."""

    return pd.read_csv(csv_path)


st.title("🌋 Interfaccia Pipeline Sismologica Geospaziale")

st.markdown(
    "Pannello di controllo web per eseguire analisi sismiche e visualizzare i risultati."
)


# SIDEBAR: CONFIGURAZIONE PARAMETRI

with st.sidebar:

    st.header("⚙️ Configurazione")

    run_name = st.text_input("Nome Esecuzione (Run Name)", value="analisi_web_01")

    st.subheader("📁 File di Input")

    use_existing_files = st.radio(
        "Sorgente dati:",
        ["File locali", "Carica nuovi file"],
        help="Scegli se usare file già presenti nella cartella data/raw/ o caricarne di nuovi",
    )

    # Inizializza variabili file per evitare NameError

    events_csv = ""

    picks_csv = ""

    stations_csv = str(EXAMPLE_LEGACY_DIR / "stations.csv")

    delta_csv = str(EXAMPLE_LEGACY_DIR / "scoperte_automatiche.csv.gz")

    if use_existing_files == "File locali":

        events_csv = st.text_input(
            "Percorso Events CSV",
            value="",
            help="Se non hai un catalogo eventi/picks, puoi usare direttamente il delta di esempio.",
        )

        picks_csv = st.text_input(
            "Percorso Picks CSV",
            value="",
            help="Facoltativo se fornisci già un file delta pre-elaborato.",
        )

        stations_csv = st.text_input(
            "Percorso Stations CSV",
            value=str(EXAMPLE_LEGACY_DIR / "stations.csv"),
            help="Catalogo stazioni usato dalla pipeline e dall'esempio legacy integrato.",
        )

        delta_csv = st.text_input(
            "Percorso Delta CSV (opzionale)",
            value=str(EXAMPLE_LEGACY_DIR / "scoperte_automatiche.csv.gz"),
            help="CSV gzip già pronto con i delta. Con il dataset di esempio la pipeline parte subito.",
        )

    else:

        st.markdown("**Carica i file CSV richiesti:**")

        uploaded_events = st.file_uploader(
            "Events CSV", type=["csv"], help="File CSV contenente gli eventi sismici"
        )

        uploaded_picks = st.file_uploader(
            "Picks CSV", type=["csv"], help="File CSV contenente i picks (fasi P/S)"
        )

        uploaded_stations = st.file_uploader(
            "Stations CSV",
            type=["csv"],
            help="File CSV contenente le coordinate delle stazioni",
        )

        uploaded_delta = st.file_uploader(
            "Delta CSV (opzionale)",
            type=["csv"],
            help="File CSV pre-processato con i delta (opzionale)",
        )

        if uploaded_events or uploaded_picks or uploaded_stations or uploaded_delta:

            data_raw_dir = PROJECT_ROOT / "data" / "raw"

            data_raw_dir.mkdir(parents=True, exist_ok=True)

            if uploaded_events:

                events_csv = str(data_raw_dir / uploaded_events.name)

                with open(events_csv, "wb") as f:

                    f.write(uploaded_events.getbuffer())

                st.success(f"Events CSV salvato in {events_csv}")

            else:

                events_csv = ""

            if uploaded_picks:

                picks_csv = str(data_raw_dir / uploaded_picks.name)

                with open(picks_csv, "wb") as f:

                    f.write(uploaded_picks.getbuffer())

                st.success(f"Picks CSV salvato in {picks_csv}")

            else:

                picks_csv = ""

            if uploaded_stations:

                stations_csv = str(data_raw_dir / uploaded_stations.name)

                with open(stations_csv, "wb") as f:

                    f.write(uploaded_stations.getbuffer())

                st.success(f"Stations CSV salvato in {stations_csv}")

            else:

                stations_csv = ""

            if uploaded_delta:

                delta_csv = str(data_raw_dir / uploaded_delta.name)

                with open(delta_csv, "wb") as f:

                    f.write(uploaded_delta.getbuffer())

                st.success(f"Delta CSV salvato in {delta_csv}")

            else:

                delta_csv = ""

    st.subheader("📍 Filtro Spaziale (Fase 0)")

    use_spatial_filter = st.checkbox("Applica Filtro Spaziale", value=True)

    if use_spatial_filter:

        lat = st.number_input("Latitudine (Punto Focale)", value=40.82, format="%.4f")

        lon = st.number_input("Longitudine (Punto Focale)", value=14.14, format="%.4f")

        radius = st.number_input("Raggio (km)", value=20.0, format="%.1f")

    st.subheader("📡 Acquisizione (Fase 1)")

    run_download = st.checkbox(
        "Scarica Tracce (MiniSEED)",
        value=False,
        help="Scarica le forme d'onda dal server FDSN per le stazioni selezionate.",
    )

    if run_download:

        st.warning(
            "⚠️ Il download di lunghi periodi richiede svariati GB. Seleziona una finestra temporale breve (es. pochi giorni)."
        )

        dl_start = st.date_input(
            "Data Inizio", value=pd.to_datetime("today") - pd.Timedelta(3, unit="D")
        )

        dl_end = st.date_input("Data Fine", value=pd.to_datetime("today"))

    st.subheader("⚙️ Controllo Fasi Pipeline")

    start_phase = st.select_slider(
        "Fase di partenza",
        options=[0, 1, 2, 3, 4],
        value=0,
        help="Seleziona da quale fase iniziare l'esecuzione (0=Selezione Spaziale, 1=Acquisizione, 2=Delta, 3=Spazializzazione, 4=GIS)",
    )

    st.markdown("**Salta fasi specifiche:**")

    col1, col2 = st.columns(2)

    with col1:

        skip_phase0 = st.checkbox("Salta Fase 0", value=False)

        skip_phase1 = st.checkbox("Salta Fase 1", value=False)

        skip_phase2 = st.checkbox("Salta Fase 2", value=False)

    with col2:

        skip_phase3 = st.checkbox("Salta Fase 3", value=False)

        skip_phase4 = st.checkbox("Salta Fase 4", value=False)

    # NOVITA: Analisi Mobile e Allarmi

    st.subheader("📱 Analisi Mobile e Allarmi")

    mobile_analysis_enabled = st.checkbox(
        "Abilita Analisi Mobile",
        value=False,
        help="Esegui analisi mobile e generazione allarmi dopo la pipeline principale",
    )

    if mobile_analysis_enabled:

        mobile_min_stations = st.slider(
            "Minimo Stazioni per Allarme",
            min_value=1,
            max_value=50,
            value=18,
            help="Soglia minima di stazioni per generare allarmi (default: 18)",
        )

        mobile_alert_threshold = st.slider(
            "Soglia Rischio per Allarme",
            min_value=0.0,
            max_value=1.0,
            value=0.7,
            step=0.01,
            format="%.2f",
            help="Soglia di probabilita per generare allarmi (default: 0.7)",
        )

        mobile_model_type = st.selectbox(
            "Tipo Modello ML",
            options=["compare", "xgboost", "random_forest", "transformer"],
            index=0,
            help="Seleziona il tipo di modello ML. Scegliendo 'compare' verranno addestrati tutti e verrà salvato automaticamente il migliore.",
        )

        mobile_generate_alerts = st.checkbox(
            "Genera Allarmi Attivi",
            value=True,
            help="Genera allarmi attivi (email, webhook, SMS) durante l'analisi mobile",
        )

    st.subheader("🔮 Predizione Live (Inferenza)")

    live_predict_enabled = st.checkbox("Esegui Predizione Live")

    if live_predict_enabled:

        # Cerca i modelli disponibili

        model_dir = PROJECT_ROOT / "mobile" / "models"

        available_models = (
            [m.name for m in model_dir.glob("*.pkl")]
            + [m.name for m in model_dir.glob("*.pth")]
            if model_dir.exists()
            else []
        )

        selected_model = st.selectbox(
            "Seleziona Modello Addestrato",
            options=available_models,
            help="Scegli il modello da usare per calcolare il rischio in tempo reale.",
        )

        live_data_csv = st.text_input(
            "File Dati (CSV)", value=str(EXAMPLE_LEGACY_DIR / "dataset_ml_sismico.csv")
        )

        live_threshold = st.slider(
            "Soglia Allarme Live", min_value=0.0, max_value=1.0, value=0.7, step=0.01
        )

        live_predict_button = st.button(
            "🔮 Calcola Rischio Ora", use_container_width=True
        )

    st.markdown("---")

    run_button = st.button(
        "🚀 Avvia Pipeline", type="primary", use_container_width=True
    )

    spatial_csv = PROJECT_ROOT / "runs" / run_name / "processed" / "deltas_spatial.csv"

    delta_range = None

    search_station = None

    if spatial_csv.exists():

        st.markdown("---")

        st.header("🗺️ Filtri Mappa")

        df_temp = load_spatial_data(str(spatial_csv), spatial_csv.stat().st_mtime)

        if not df_temp.empty and "delta_seconds" in df_temp.columns:

            valid_deltas = df_temp["delta_seconds"].dropna()

            if not valid_deltas.empty:

                min_d = float(valid_deltas.min())

                max_d = float(valid_deltas.max())

                if min_d >= max_d:

                    min_d, max_d = min_d - 1.0, max_d + 1.0

                delta_range = st.slider(
                    "Filtra per Delta (secondi)",
                    min_value=min_d,
                    max_value=max_d,
                    value=(min_d, max_d),
                )

        search_station = st.text_input(
            "🔍 Cerca Stazione (es. CAAM)", help="Filtra per nome della stazione"
        )


# Navigazione

col_nav1, col_nav2 = st.columns([1, 4])

with col_nav1:

    if st.button("🚨 Dashboard Allarmi"):
        st.switch_page("alerts_dashboard")


# LOGICA DI ESECUZIONE

if run_button:

    cmd_pipeline = [
        sys.executable,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--run-name",
        run_name,
    ]

    for flag, value in [
        ("--events-csv", events_csv),
        ("--picks-csv", picks_csv),
        ("--stations-csv", stations_csv),
        ("--delta-csv", delta_csv),
    ]:
        if value:
            cmd_pipeline.extend([flag, value])

    cmd_pipeline.extend(["--start-phase", str(start_phase)])

    if skip_phase0:

        cmd_pipeline.append("--skip-phase0")

    if skip_phase1:

        cmd_pipeline.append("--skip-phase1")

    if skip_phase2:

        cmd_pipeline.append("--skip-phase2")

    if skip_phase3:

        cmd_pipeline.append("--skip-phase3")

    if skip_phase4:

        cmd_pipeline.append("--skip-phase4")

    # NOVITA: Aggiungi parametri analisi mobile

    if mobile_analysis_enabled:

        cmd_pipeline.append("--mobile-analysis")

        cmd_pipeline.extend(["--mobile-min-stations", str(mobile_min_stations)])

        cmd_pipeline.extend(["--mobile-alert-threshold", str(mobile_alert_threshold)])

        cmd_pipeline.extend(["--mobile-model-type", mobile_model_type])

        if mobile_generate_alerts:

            cmd_pipeline.append("--mobile-generate-alerts")

    status_msg = st.empty()

    status_msg.info(
        f"Avvio della run: **{run_name}** in corso. Attendi il completamento...",
        icon="⏳",
    )

    if use_spatial_filter and not skip_phase0:

        with st.spinner("Fase 0: Estrazione stazioni nell'area selezionata..."):

            cmd_fase0 = [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "select_stations_spatial.py"),
                "--input-csv",
                stations_csv,
                "--point",
                str(lat),
                str(lon),
                str(radius),
                "--output-file",
                str(PROJECT_ROOT / "runs" / run_name / "selected_stations.txt"),
            ]

            try:

                res0 = subprocess.run(
                    cmd_fase0, capture_output=True, text=True, check=True
                )

                st.success("Selezione spaziale completata!")

            except subprocess.CalledProcessError as e:

                st.error("Errore durante la Fase 0 (Selezione Spaziale)")

                st.code(e.stderr)

                st.stop()

    if run_download and not skip_phase1:

        st.info(
            "Fase 1: Download delle tracce MiniSEED in corso. Leggi i log qui sotto in tempo reale..."
        )

        cmd_fase1 = [
            sys.executable,
            "-u",
            str(PROJECT_ROOT / "scripts" / "download_cf_waveforms.py"),
            "--start",
            dl_start.strftime("%Y-%m-%dT00:00:00"),
            "--end",
            dl_end.strftime("%Y-%m-%dT23:59:59"),
        ]

        if use_spatial_filter:
            cmd_fase1 += [
                "--stations-file",
                str(PROJECT_ROOT / "runs" / run_name / "selected_stations.txt"),
            ]

        log_box = st.empty()
        log_text = ""

        process = subprocess.Popen(
            cmd_fase1,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        if process.stdout is not None:
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    log_text += line
                    lines = log_text.splitlines()
                    if len(lines) > 25:
                        log_text = "\n".join(lines[-25:]) + "\n"
                    log_box.code(log_text, language="bash")

            process.stdout.close()

        if process.wait() != 0:
            st.error("❌ Errore durante il download delle tracce (Fase 1)")
            st.stop()

        st.success("✅ Download tracce completato!")

    with st.spinner("Esecuzione delle analisi spaziali..."):

        try:

            res_pipe = subprocess.run(
                cmd_pipeline, capture_output=True, text=True, check=True
            )

            st.success("Pipeline completata con successo! 🎉")

            status_msg.empty()

            with st.expander("Mostra Log Dettagliati dell'Orchestratore"):

                st.code(res_pipe.stdout)

        except subprocess.CalledProcessError as e:

            st.error("Errore critico durante l'esecuzione della pipeline!")

            st.code(e.stderr)

            st.stop()


if (
    "live_predict_enabled" in locals()
    and live_predict_enabled
    and "live_predict_button" in locals()
    and live_predict_button
    and selected_model
):

    cmd_live = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "predict_live.py"),
        "--model",
        str(PROJECT_ROOT / "mobile" / "models" / selected_model),
        "--data",
        live_data_csv,
        "--threshold",
        str(live_threshold),
    ]

    with st.spinner("🔮 Calcolo del rischio in corso..."):

        try:

            res_live = subprocess.run(
                cmd_live, capture_output=True, text=True, check=True
            )

            # Estrai la probabilità dall'output per visualizzarla

            output_lines = res_live.stdout.splitlines() + res_live.stderr.splitlines()

            st.success("Analisi in Tempo Reale completata!")

            with st.expander("Mostra Dettagli Predizione Live", expanded=True):

                st.code("\n".join(output_lines[-15:]))

        except subprocess.CalledProcessError as e:

            st.error("Errore durante l'inferenza!")

            st.code(e.stderr)


# VISUALIZZAZIONE RISULTATI

st.header("🗺️ Mappa Interattiva delle Stazioni")

spatial_csv = PROJECT_ROOT / "runs" / run_name / "processed" / "deltas_spatial.csv"


if spatial_csv.exists():

    df_spatial = load_spatial_data(str(spatial_csv), spatial_csv.stat().st_mtime)

    if (
        not df_spatial.empty
        and "latitude" in df_spatial.columns
        and "longitude" in df_spatial.columns
    ):

        if delta_range is not None:

            df_spatial = df_spatial[
                (df_spatial["delta_seconds"] >= delta_range[0])
                & (df_spatial["delta_seconds"] <= delta_range[1])
            ]

        if search_station and df_spatial is not None:

            df_spatial = df_spatial[
                df_spatial["station"].str.contains(search_station.upper(), na=False)
            ]

        center_lat = df_spatial["latitude"].mean() if not df_spatial.empty else 40.82

        center_lon = df_spatial["longitude"].mean() if not df_spatial.empty else 14.14

        m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles=None)

        folium.TileLayer("CartoDB positron", name="CartoDB Light (Default)").add_to(m)

        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri",
            name="Esri Satellite",
        ).add_to(m)

        folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)

        Fullscreen(position="topright").add_to(m)

        stazioni_group = folium.FeatureGroup(name="Stazioni Sismiche").add_to(m)

        df_delays = df_spatial[df_spatial["delta_seconds"] > 0]

        if not df_delays.empty:

            heat_data = df_delays[
                ["latitude", "longitude", "delta_seconds"]
            ].values.tolist()

            heat_map_group = folium.FeatureGroup(
                name="HeatMap Ritardi", show=False
            ).add_to(m)

            HeatMap(heat_data, radius=25, blur=15, name="HeatMap Ritardi").add_to(
                heat_map_group
            )

        for _, row in df_spatial.iterrows():

            delta = row.get("delta_seconds", 0)

            color = "crimson" if delta > 0.1 else "darkblue" if delta < -0.1 else "gray"

            popup_html = f"<b>Stazione: {row['station']}</b><br>Delta: {delta:.3f} s"

            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=7,
                popup=folium.Popup(popup_html, max_width=200),
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
            ).add_to(stazioni_group)

        folium.LayerControl(position="topright").add_to(m)

        st_folium(m, use_container_width=True, height=500, returned_objects=[])

        st.markdown("### 📋 Dati Stazioni Filtrati")

        if not df_spatial.empty:

            st.dataframe(
                df_spatial.sort_values("delta_seconds", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

            st.caption(f"Stazioni visualizzate: {len(df_spatial)}")

        else:

            st.warning("Nessuna stazione corrisponde ai criteri di filtro impostati.")


st.header("📊 Mappe e Grafici Generati")

maps_dir = PROJECT_ROOT / "runs" / run_name / "maps"


if maps_dir.exists():

    images = list(maps_dir.glob("*.png"))

    if images:

        cols = st.columns(2)

        for i, img_path in enumerate(images):

            with cols[i % 2]:

                st.image(str(img_path), caption=img_path.name, use_container_width=True)


# ESPORTAZIONE RISULTATI

run_folder = PROJECT_ROOT / "runs" / run_name

if run_folder.exists() and any(run_folder.iterdir()):

    st.markdown("---")

    st.header("💾 Esporta Risultati")

    st.write(
        "Scarica l'intera cartella dell'esecuzione (inclusi file CSV, Shapefile, GeoTIFF e immagini grafiche)."
    )

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
        type="primary",
    )
