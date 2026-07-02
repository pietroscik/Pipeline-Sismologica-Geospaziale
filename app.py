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

def list_available_runs(runs_root: Path) -> list[str]:
    if not runs_root.exists():
        return []
    runs = [p for p in runs_root.iterdir() if p.is_dir()]
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in runs]

def load_spatial_data_from_runs(runs_root: Path, run_names: list[str]) -> pd.DataFrame:
    frames = []
    for rn in run_names:
        csv_path = runs_root / rn / "processed" / "deltas_spatial.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            df["run_name"] = rn
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def run_pipeline_streamlit(cmd: list, log_placeholder):
    """Esegue un comando e mostra l'output in tempo reale su Streamlit."""
    log_text = ""
    log_placeholder.info("Avvio del processo in background...", icon="⏳")

    # Assicura che tutti gli argomenti del comando siano stringhe
    cmd_str = [str(arg) for arg in cmd]
    st.code(f"▶️ Esecuzione: {' '.join(cmd_str)}")

    process = subprocess.Popen(
        cmd_str,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding='utf-8'
    )

    log_box = st.empty()
    if process.stdout:
        for line in iter(process.stdout.readline, ""):
            log_text += line
            # Mantiene solo le ultime 50 righe per non sovraccaricare il DOM
            lines = log_text.splitlines()
            if len(lines) > 50:
                log_text = "\n".join(lines[-50:]) + "\n"
            log_box.code(log_text, language="bash")
        process.stdout.close()

    return_code = process.wait()
    return return_code


st.title("🌋 Interfaccia Pipeline Sismologica Geospaziale")

st.markdown(
    "Pannello di controllo web per eseguire analisi sismiche e visualizzare i risultati."
)


# SIDEBAR: CONFIGURAZIONE PARAMETRI

with st.sidebar:

    st.header("⚙️ Configurazione")

    run_name = st.text_input("Nome Esecuzione (Run Name)", value="analisi_web_01")

    # --- SEZIONI RIORGANIZZATE PER ORDINE LOGICO ---

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

    # --- FINE SEZIONI RIORGANIZZATE ---

    st.subheader("📁 File di Input")

    use_existing_files = st.radio(
        "Sorgente dati:",
        ["File locali", "Carica nuovi file"],
        help="Scegli se usare file già presenti nella cartella data/raw/ o caricarne di nuovi",
    )

    # Inizializza variabili file per evitare NameError

    events_csv: Path | str = ""

    picks_csv: Path | str = ""

    stations_csv: Path | str = EXAMPLE_LEGACY_DIR / "stations.csv"

    delta_csv: Path | str = EXAMPLE_LEGACY_DIR / "scoperte_automatiche.csv.gz"

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
            value=str(stations_csv),
            help="Catalogo stazioni usato dalla pipeline e dall'esempio legacy integrato.",
        )

        # Logica per disabilitare il campo delta se il download è attivo
        disable_delta_input = run_download
        if disable_delta_input:
            st.warning(
                "Il download è attivo, quindi qualsiasi Delta CSV pre-esistente verrà ignorato per processare i nuovi dati."
            )
            delta_csv_value = ""
        else:
            delta_csv_value = str(delta_csv)

        delta_csv = st.text_input(
            "Percorso Delta CSV (opzionale)",
            value=delta_csv_value,
            help="CSV gzip già pronto con i delta. Con il dataset di esempio la pipeline parte subito.",
            disabled=disable_delta_input,
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

            def save_uploaded_file(uploaded_file):
                if uploaded_file:
                    target_path = data_raw_dir / uploaded_file.name
                    with open(target_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    st.success(f"File salvato: {target_path}")
                    return target_path
                return ""

            events_csv = save_uploaded_file(uploaded_events)
            picks_csv = save_uploaded_file(uploaded_picks)
            stations_csv = save_uploaded_file(uploaded_stations)
            delta_csv = save_uploaded_file(uploaded_delta)

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
        PROJECT_ROOT / "run_pipeline.py",
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

        cmd_pipeline.extend(["--mobile-min-stations", mobile_min_stations])

        cmd_pipeline.extend(["--mobile-alert-threshold", mobile_alert_threshold])

        cmd_pipeline.extend(["--mobile-model-type", mobile_model_type])

        if mobile_generate_alerts:

            cmd_pipeline.append("--mobile-generate-alerts")

    # Aggiungi parametri per il download se abilitato
    if run_download and not skip_phase1:
        cmd_pipeline.append("--run-download")
        cmd_pipeline.extend(["--download-start", dl_start.strftime("%Y-%m-%d")])
        cmd_pipeline.extend(["--download-end", dl_end.strftime("%Y-%m-%d")])

    status_msg = st.empty()
    log_placeholder = st.empty()

    return_code = run_pipeline_streamlit(cmd_pipeline, log_placeholder)

    if return_code == 0:
        status_msg.success(f"Pipeline per la run **{run_name}** completata con successo! 🎉")
        st.balloons()
    else:
        status_msg.error(f"Errore durante l'esecuzione della pipeline per la run **{run_name}**. Controlla i log qui sopra. (Codice: {return_code})")


if (
    "live_predict_enabled" in locals()
    and live_predict_enabled
    and "live_predict_button" in locals()
    and live_predict_button
    and selected_model
):

    cmd_live = [
        sys.executable,
        PROJECT_ROOT / "scripts" / "predict_live.py",
        "--model",
        PROJECT_ROOT / "mobile" / "models" / selected_model,
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

runs_root = PROJECT_ROOT / "runs"
available_runs = list_available_runs(runs_root)

# Definiamo i widget per selezionare la modalità di visualizzazione
if available_runs:
    consult_mode = st.radio(
        "Modalità di visualizzazione",
        ["Visualizza una singola esecuzione", "Merge tutte le esecuzioni"],
        horizontal=True,
        key="consult_mode_radio",
    )

    selected_run_for_view = None
    if consult_mode == "Visualizza una singola esecuzione":
        selected_run_for_view = st.selectbox(
            "Seleziona una run da visualizzare", options=available_runs, key="run_selector"
        )
else:
    # Se non ci sono run, impostiamo valori di default per evitare errori
    consult_mode = "Visualizza una singola esecuzione"
    selected_run_for_view = None
    st.info("Nessuna esecuzione ('run') trovata nella cartella `runs/`. Esegui una pipeline per vedere i risultati.")

# Carichiamo i dati in base alla modalità scelta
df_spatial = pd.DataFrame()
if consult_mode == "Merge tutte le esecuzioni":
    if available_runs:
        df_spatial = load_spatial_data_from_runs(runs_root, available_runs)
elif selected_run_for_view:
    spatial_csv = runs_root / selected_run_for_view / "processed" / "deltas_spatial.csv"
    if spatial_csv.exists():
        df_spatial = load_spatial_data(str(spatial_csv), spatial_csv.stat().st_mtime)


if (
    not df_spatial.empty
    and "latitude" in df_spatial.columns
    and "longitude" in df_spatial.columns
):
    if not df_spatial.empty:
        st.dataframe(
            df_spatial.sort_values("delta_seconds", ascending=False),
            use_container_width=True,
            hide_index=True,
        )
        caption = f"Stazioni visualizzate: {len(df_spatial)}"
        if "run_name" in df_spatial.columns:
            caption += f" | Run incluse: {df_spatial['run_name'].nunique()}"
        st.caption(caption)


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
