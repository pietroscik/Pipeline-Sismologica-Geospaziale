import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import folium
import pandas as pd
import plotly.express as px
import streamlit as st
from folium.plugins import Fullscreen, HeatMap, MarkerCluster
from streamlit_folium import st_folium


def _resolve_project_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


PROJECT_ROOT = _resolve_project_root()

SEVERITY_COLORS = {
    "low": "#4bff4b",
    "medium": "#ffa500",
    "high": "#ff4b4b",
    "critical": "#800080",
}

DEFAULT_STATIONS = [
    "CAAM", "CAFL", "CAWE", "CBAC", "CBAG", "CCAP", "CFMN", "CMIS", "CMSN", "CMTS",
    "CNIS", "COLB", "CPIS", "CPOZ", "CQUE", "CSFT", "CSOB", "CSTH", "CUMA", "IBCM",
    "IBRN", "IOCA", "IPSM", "PTMR",
]


def classify_severity(risk_score: float) -> str:
    if risk_score >= 0.85:
        return "critical"
    if risk_score >= 0.65:
        return "high"
    if risk_score >= 0.40:
        return "medium"
    return "low"


def _candidate_monitor_dirs() -> list[Path]:
    return [
        PROJECT_ROOT / "runs" / "monitor",
        PROJECT_ROOT / "mobile" / "runs" / "monitor",
        PROJECT_ROOT / "runs",
        PROJECT_ROOT / "mobile" / "runs",
    ]


def _iter_data_files(days: int) -> list[Path]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    files: list[Path] = []
    patterns = ("*.json", "*.jsonl", "*.csv", "*.csv.gz", "*.parquet")
    for d in _candidate_monitor_dirs():
        if not d.exists():
            continue
        for ptn in patterns:
            for f in d.rglob(ptn):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) >= cutoff:
                        files.append(f)
                except Exception:
                    continue
    return files


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return []
    if path.suffix == ".jsonl":
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out
    try:
        obj = json.loads(text)
    except Exception:
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for key in ("alerts", "events", "results", "data", "items"):
            if isinstance(obj.get(key), list):
                return [x for x in obj[key] if isinstance(x, dict)]
        return [obj]
    return []


def _normalize_alert(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "time": "timestamp",
        "datetime": "timestamp",
        "created_at": "timestamp",
        "alert_time": "timestamp",
        "station_code": "station",
        "station_id": "station",
        "risk": "risk_score",
        "score": "risk_score",
        "probability": "risk_score",
        "lat": "latitude",
        "lon": "longitude",
        "lng": "longitude",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    if "timestamp" not in df.columns:
        df["timestamp"] = pd.NaT
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)

    # fallback su colonne epoch note
    if df["timestamp"].isna().all():
        for c in ("event_reference_epoch", "arrival_epoch", "start_epoch", "end_epoch", "time_epoch"):
            if c in df.columns:
                s = pd.to_numeric(df[c], errors="coerce")
                if s.notna().any():
                    df["timestamp"] = pd.to_datetime(s, unit="s", errors="coerce", utc=True)
                    break

    # fallback finale: mtime file sorgente
    if df["timestamp"].isna().all() and "__source_mtime_utc" in df.columns:
        df["timestamp"] = pd.to_datetime(df["__source_mtime_utc"], errors="coerce", utc=True)

    if "station" not in df.columns:
        df["station"] = "UNKNOWN"

    if "risk_score" not in df.columns:
        df["risk_score"] = 0.0
    df["risk_score"] = pd.to_numeric(df["risk_score"], errors="coerce").fillna(0.0).clip(0, 1)

    if "severity" not in df.columns:
        df["severity"] = df["risk_score"].apply(classify_severity)
    else:
        df["severity"] = (
            df["severity"].astype(str).str.lower().replace({"warn": "medium", "alert": "high"})
        )

    for col in ("latitude", "longitude"):
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "message" not in df.columns:
        df["message"] = ""

    cols = ["timestamp", "station", "risk_score", "severity", "latitude", "longitude", "message"]
    if "__source_file" in df.columns:
        cols.append("__source_file")
    return df[cols].copy()


@st.cache_data(ttl=180)
def load_alert_history(days: int = 7) -> tuple[pd.DataFrame, dict[str, Any]]:
    files = _iter_data_files(days)
    frames: list[pd.DataFrame] = []

    for f in files:
        try:
            if f.suffix in (".json", ".jsonl"):
                recs = _load_json_records(f)
                if recs:
                    df = pd.DataFrame(recs)
                else:
                    continue
            elif f.suffix in (".csv", ".gz") or f.name.endswith(".csv.gz"):
                df = pd.read_csv(f)
            elif f.suffix == ".parquet":
                df = pd.read_parquet(f)
            else:
                continue

            df["__source_file"] = str(f)
            df["__source_mtime_utc"] = pd.to_datetime(f.stat().st_mtime, unit="s", utc=True)
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame(columns=["timestamp", "station", "risk_score", "severity", "latitude", "longitude", "message"]), {
            "files_scanned": len(files),
            "records_loaded": 0,
            "records_after_filter": 0,
            "candidate_dirs": [str(d) for d in _candidate_monitor_dirs()],
        }

    raw = pd.concat(frames, ignore_index=True, sort=False)
    alerts = _normalize_alert(raw).sort_values("timestamp", ascending=False, na_position="last")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    if alerts["timestamp"].notna().any():
        alerts = alerts[alerts["timestamp"] >= cutoff].copy()
    # se tutti NaT, non filtrare: mostra comunque i record

    return alerts, {
        "files_scanned": len(files),
        "records_loaded": len(raw),
        "records_after_filter": len(alerts),
        "candidate_dirs": [str(d) for d in _candidate_monitor_dirs()],
    }


st.set_page_config(page_title="Dashboard Allarmi - Campi Flegrei", layout="wide")
st.title("Dashboard Allarmi - Campi Flegrei")
st.markdown("Visualizzazione in tempo reale degli allarmi generati.")

with st.sidebar:
    st.header("Filtri")
    days_back = st.slider("Giorni storico", min_value=1, max_value=30, value=7)
    show_all_time = st.checkbox("Ignora filtro temporale (mostra tutto)", value=False)
    show_debug = st.checkbox("Debug loader", value=True)

alerts_df, dbg = load_alert_history(days=days_back)
if show_all_time:
    # ricarico senza taglio temporale forte
    alerts_df, dbg = load_alert_history(days=3650)

if show_debug:
    with st.sidebar.expander("Debug", expanded=False):
        st.write(dbg)

st.markdown("## Statistiche")
if alerts_df.empty:
    st.info("Nessun allarme trovato.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Totale allarmi", f"{len(alerts_df):,}")
    c2.metric("Ultime 24h", f"{(alerts_df['timestamp'] >= (pd.Timestamp.utcnow() - pd.Timedelta(hours=24))).sum():,}")
    c3.metric("Risk medio", f"{alerts_df['risk_score'].mean():.2f}")
    c4.metric("Stazioni attive", f"{alerts_df['station'].nunique():,}")

st.markdown("---")
st.markdown("## Timeline Allarmi")
if alerts_df.empty:
    st.info("Nessun dato disponibile.")
else:
    timeline = (
        alerts_df.set_index("timestamp")
        .resample("1H")
        .size()
        .rename("count")
        .reset_index()
    )
    fig = px.line(timeline, x="timestamp", y="count", title="Allarmi per ora")
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.markdown("## Mappa Stazioni")
m = folium.Map(location=[40.827, 14.139], zoom_start=11, control_scale=True)
Fullscreen().add_to(m)

geo = alerts_df.dropna(subset=["latitude", "longitude"]) if not alerts_df.empty else pd.DataFrame()
if not geo.empty:
    HeatMap(geo[["latitude", "longitude"]].values.tolist(), radius=14, blur=12).add_to(m)
    cluster = MarkerCluster(name="Allarmi").add_to(m)

    radius_by_severity = {"low": 5, "medium": 7, "high": 9, "critical": 11}
    for _, r in geo.head(1000).iterrows():
        sev = str(r["severity"]).lower()
        folium.CircleMarker(
            location=[float(r["latitude"]), float(r["longitude"])],
            radius=radius_by_severity.get(sev, 6),
            color=SEVERITY_COLORS.get(sev, "#3388ff"),
            weight=2,
            fill=True,
            fill_opacity=0.85,
            popup=f"{r['station']} | {sev} | score={r['risk_score']:.2f}",
        ).add_to(cluster)

st_folium(m, width=None, height=520)

st.markdown("---")
st.markdown("## Allarmi Recenti")
if alerts_df.empty:
    st.info("Nessun allarme trovato.")
else:
    st.dataframe(
        alerts_df[["timestamp", "station", "severity", "risk_score", "message"]].head(200),
        use_container_width=True,
        hide_index=True,
    )

st.markdown("---")
st.caption("Dashboard Allarmi - Issue #5 - FASE 5.2")
