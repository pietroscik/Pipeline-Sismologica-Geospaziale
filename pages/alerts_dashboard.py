import streamlit as st
from pathlib import Path
from datetime import datetime, timedelta
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import folium
from folium.plugins import Fullscreen, HeatMap
from streamlit_folium import st_folium

from path_utils import PROJECT_ROOT

st.set_page_config(page_title="Dashboard Allarmi", page_icon="🚨", layout="wide")

MONITOR_RESULTS_DIR = PROJECT_ROOT / "runs" / "monitor"

SEVERITY_COLORS = {
    "low": "#4bff4b",
    "medium": "#ffa500",
    "high": "#ff4b4b",
    "critical": "#800080"
}

SEVERITY_ICONS = {
    "low": "OK",
    "medium": "WARNING",
    "high": "ALERT",
    "critical": "CRITICAL"
}

DEFAULT_STATIONS = [
    "CAAM", "CAFL", "CAWE", "CBAC", "CBAG", "CCAP", "CFMN", "CMIS",
    "CMSN", "CMTS", "CNIS", "COLB", "CPIS", "CPOZ", "CQUE", "CSFT",
    "CSOB", "CSTH", "CUMA", "IBCM", "IBRN", "IOCA", "IPSM", "PTMR"
]

@st.cache_data(ttl=300)
def load_alert_history(days):
    alerts = []
    if not MONITOR_RESULTS_DIR.exists():
        return alerts
    cutoff = datetime.now() - timedelta(days=days)
    for summary_file in sorted(MONITOR_RESULTS_DIR.glob("summary_*.json"), reverse=True):
        try:
            with open(summary_file, 'r') as f:
                data = json.load(f)
            alert_date = datetime.fromisoformat(data.get("timestamp", "1970-01-01"))
            if alert_date >= cutoff:
                severity = classify_severity(data.get("risk_score", 0))
                alerts.append({
                    "timestamp": alert_date,
                    "risk_score": data.get("risk_score", 0),
                    "alert_required": data.get("alert_required", False),
                    "alert_message": data.get("alert_message", ""),
                    "stations_count": data.get("stations_count", 0),
                    "records_count": data.get("records_count", 0),
                    "severity": severity
                })
        except:
            continue
    return alerts

@st.cache_data(ttl=300)
def load_station_data():
    stations_csv = PROJECT_ROOT / "examples" / "mobile_devices" / "stations.csv"
    if stations_csv.exists():
        df = pd.read_csv(stations_csv)
        for station in DEFAULT_STATIONS:
            if station not in df["station"].values:
                df = pd.concat([df, pd.DataFrame({
                    "station": [station],
                    "latitude": [40.8062],
                    "longitude": [14.1410],
                    "elevation": [0]
                })], ignore_index=True)
        return df
    return pd.DataFrame({
        "station": DEFAULT_STATIONS,
        "latitude": [40.8062] * len(DEFAULT_STATIONS),
        "longitude": [14.1410] * len(DEFAULT_STATIONS),
        "elevation": [0] * len(DEFAULT_STATIONS)
    })

def classify_severity(risk_score):
    if risk_score >= 0.9:
        return "critical"
    elif risk_score >= 0.7:
        return "high"
    elif risk_score >= 0.4:
        return "medium"
    else:
        return "low"

st.title("Dashboard Allarmi - Campi Flegrei")
st.markdown("Visualizzazione in tempo reale degli allarmi generati.")

with st.sidebar:
    st.header("Filtri")
    days_back = st.slider("Ultimi N giorni", 1, 30, 7)
    severity_options = ["Tutti"] + list(SEVERITY_COLORS.keys())
    selected_severities = st.multiselect("Severita:", severity_options, default=["Tutti"])
    risk_range = st.slider("Range rischio:", 0.0, 1.0, (0.0, 1.0), format="%.2f")

alerts = load_alert_history(days=days_back)
stations_df = load_station_data()

filtered_alerts = []
for alert in alerts:
    if "Tutti" not in selected_severities and alert["severity"] not in selected_severities:
        continue
    if not (risk_range[0] <= alert["risk_score"] <= risk_range[1]):
        continue
    filtered_alerts.append(alert)

st.markdown("## Statistiche")
if filtered_alerts:
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Totale Allarmi", len(filtered_alerts))
    with col2:
        st.metric("Critici", sum(1 for a in filtered_alerts if a["severity"] == "critical"))
    with col3:
        st.metric("Alti", sum(1 for a in filtered_alerts if a["severity"] == "high"))
    with col4:
        st.metric("Medio", sum(1 for a in filtered_alerts if a["severity"] == "medium"))
    with col5:
        st.metric("Bassi", sum(1 for a in filtered_alerts if a["severity"] == "low"))
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        avg_risk = sum(a["risk_score"] for a in filtered_alerts) / len(filtered_alerts)
        st.metric("Rischio Medio", f"{avg_risk:.3f}")
    with col2:
        max_risk = max(a["risk_score"] for a in filtered_alerts)
        st.metric("Rischio Massimo", f"{max_risk:.3f}")
    with col3:
        if len(filtered_alerts) > 1:
            time_diffs = []
            for i in range(1, len(filtered_alerts)):
                diff = (filtered_alerts[i-1]["timestamp"] - filtered_alerts[i]["timestamp"]).total_seconds() / 3600
                time_diffs.append(diff)
            avg_hours = sum(time_diffs) / len(time_diffs) if time_diffs else 0
            st.metric("Tempo Medio (h)", f"{avg_hours:.1f}")
        else:
            st.metric("Tempo Medio (h)", "N/A")
    with col4:
        avg_stations = sum(a["stations_count"] for a in filtered_alerts) / len(filtered_alerts)
        st.metric("Stazioni Medie", f"{avg_stations:.1f}")
else:
    st.info("Nessun allarme trovato.")

st.markdown("---")
st.markdown("## Timeline Allarmi")

if filtered_alerts:
    timeline_data = pd.DataFrame([
        {
            "Timestamp": a["timestamp"],
            "Rischio": a["risk_score"],
            "Severita": a["severity"],
            "Stazioni": a["stations_count"]
        }
        for a in filtered_alerts
    ])
    
    fig = px.line(timeline_data, x="Timestamp", y="Rischio", title="Andamento Rischio")
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        severity_counts = pd.DataFrame({
            "Severita": ["low", "medium", "high", "critical"],
            "Conteggio": [
                sum(1 for a in filtered_alerts if a["severity"] == "low"),
                sum(1 for a in filtered_alerts if a["severity"] == "medium"),
                sum(1 for a in filtered_alerts if a["severity"] == "high"),
                sum(1 for a in filtered_alerts if a["severity"] == "critical")
            ]
        })
        fig2 = px.bar(severity_counts, x="Severita", y="Conteggio", title="Distribuzione Severita")
        st.plotly_chart(fig2, use_container_width=True)
    
    with col2:
        fig3 = px.histogram(timeline_data, x="Rischio", nbins=20, title="Distribuzione Rischio")
        st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("Nessun dato disponibile.")

st.markdown("---")
st.markdown("## Mappa Stazioni")

m = folium.Map(location=[40.8062, 14.1410], zoom_start=11, tiles="CartoDB positron")
Fullscreen().add_to(m)

if not stations_df.empty:
    for _, row in stations_df.iterrows():
        color = "#808080"
        for a in filtered_alerts:
            if row["station"] in str(a.get("alert_message", "")):
                color = SEVERITY_COLORS.get(a["severity"], "#808080")
                break
        popup_html = f"Station: {row['station']}<br>Lat: {row['latitude']:.4f}<br>Lon: {row['longitude']:.4f}"
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=8,
            popup=folium.Popup(popup_html, max_width=200),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7
        ).add_to(m)

HeatMap([[row["latitude"], row["longitude"]] for _, row in stations_df.iterrows()], radius=15).add_to(m)
folium.LayerControl(position='topright').add_to(m)
st_folium(m, use_container_width=True, height=500)

st.markdown("---")
st.markdown("## Allarmi Recenti")

if filtered_alerts:
    alerts_df = pd.DataFrame([
        {
            "Data/Ora": a["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
            "Rischio": f"{a['risk_score']:.4f}",
            "Severita": a["severity"].capitalize(),
            "Stazioni": a["stations_count"],
            "Messaggio": a["alert_message"][:50] + "..." if len(a["alert_message"]) > 50 else a["alert_message"]
        }
        for a in filtered_alerts
    ])
    st.dataframe(alerts_df, use_container_width=True, hide_index=True)
    csv = alerts_df.to_csv(index=False)
    st.download_button("Scarica CSV", data=csv, file_name=f"allarmi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv")
else:
    st.info("Nessun allarme trovato.")

st.markdown("---")
st.markdown("Dashboard Allarmi - Issue #5 - FASE 5.2")
