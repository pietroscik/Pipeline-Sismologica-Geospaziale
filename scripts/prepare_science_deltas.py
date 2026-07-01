#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from utils import load_csv_with_checks, setup_logger, get_project_root

logger = setup_logger("prepare_deltas")


def parse_args() -> argparse.Namespace:
    # Ricava dinamicamente la cartella root del progetto (due livelli sopra questo script)
    default_base = get_project_root()
    default_raw = default_base / "data" / "raw"
    parser = argparse.ArgumentParser(
        description=(
            "Costruisce un CSV stile station_deltas.csv a partire dagli output "
            "science.adw9038_data_s1/s2."
        )
    )
    parser.add_argument(
        "--events-csv",
        type=Path,
        default=default_raw / "science.adw9038_data_s1.csv",
        help="File con le informazioni degli eventi (default: science.adw9038_data_s1.csv).",
    )
    parser.add_argument(
        "--picks-csv",
        type=Path,
        default=default_raw / "science.adw9038_data_s2.csv",
        help="File con le pick P/S (default: science.adw9038_data_s2.csv).",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=default_base / "data" / "interim" / "station_deltas_science.csv",
        help="Percorso del CSV di output (default: station_deltas_science.csv).",
    )
    parser.add_argument(
        "--phase-type",
        default="P",
        help="Tipo di fase da usare (default: P).",
    )
    parser.add_argument(
        "--stations-file",
        type=Path,
        help="File TXT opzionale per filtrare specifiche stazioni (es. output di selezione spaziale).",
    )
    parser.add_argument(
        "--network-filter",
        nargs="*",
        help="Lista di network ammessi (es. IV). Se omessa, usa tutti i network.",
    )
    parser.add_argument(
        "--reference",
        default="median",
        choices=["median", "mean", "first"],
        help="Metodo per calcolare il tempo di riferimento dell'evento (default: median).",
    )
    return parser.parse_args()


def load_events(path: Path) -> pd.DataFrame:
    df = load_csv_with_checks(path, {"event_index", "TIME"})
    df = df.copy()
    df["event_index"] = df["event_index"].astype(int)
    df["event_time"] = pd.to_datetime(df["TIME"], utc=True, errors="coerce")
    df = df.dropna(subset=["event_time"])
    return df[["event_index", "event_time"]]


def split_station_id(series: pd.Series) -> pd.DataFrame:
    parts = series.str.split(".", expand=True)
    if parts.shape[1] < 2:
        raise SystemExit("Formato station_id inatteso: atteso 'NET.STAZ...'.")
    result = pd.DataFrame()
    result["network"] = parts[0].str.upper()
    result["station"] = parts[1].str.upper()
    # Usa l'ultima parte come channel (copre casi NET.STA.LOC.CHAN o NET.STA.CHAN)
    result["channel"] = parts.iloc[:, -1].str.upper()
    return result


def load_picks(path: Path, phase_type: str) -> pd.DataFrame:
    df = load_csv_with_checks(path, {"station_id", "phase_time", "phase_type", "event_index"})
    df = df.copy()
    df["phase_type"] = df["phase_type"].astype(str).str.upper()
    df = df[df["phase_type"] == phase_type.upper()]
    if df.empty:
        raise SystemExit(f"Nessuna pick con phase_type={phase_type!r} in {path}.")
    df["event_index"] = df["event_index"].astype(int)
    timing = pd.to_datetime(df["phase_time"], utc=True, errors="coerce")
    df = df[~timing.isna()].copy()
    df["phase_time"] = timing[~timing.isna()]

    station_meta = split_station_id(df["station_id"])
    df = pd.concat([df.reset_index(drop=True), station_meta], axis=1)
    return df


def main() -> None:
    args = parse_args()

    events = load_events(args.events_csv)
    picks = load_picks(args.picks_csv, args.phase_type)

    if args.network_filter:
        picks = picks[picks["network"].isin({n.upper() for n in args.network_filter})]
        if picks.empty:
            raise SystemExit("Filtrando per network non resta alcuna osservazione.")

    if args.stations_file and args.stations_file.exists():
        lines = args.stations_file.read_text(encoding="utf-8").splitlines()
        allowed = {line.strip().upper() for line in lines if line.strip() and not line.startswith("#")}
        if allowed:
            picks = picks[picks["station"].isin(allowed)]
            if picks.empty:
                raise SystemExit("Filtrando per stazioni non resta alcuna osservazione.")

    merged = picks.merge(events, on="event_index", how="left")
    merged = merged.dropna(subset=["event_time"])
    if merged.empty:
        raise SystemExit("Nessuna pick coincide con gli eventi forniti.")

    merged["arrival_epoch"] = merged["phase_time"].astype("int64") / 1e9
    merged["arrival_iso"] = merged["phase_time"].dt.strftime("%Y-%m-%d %H:%M:%S.%f%z")
    merged["event_reference_epoch"] = (
        merged.groupby("event_index")["arrival_epoch"].transform(args.reference)
    )
    merged["delta_seconds"] = merged["arrival_epoch"] - merged["event_reference_epoch"]

    output = merged[
        [
            "event_index",
            "network",
            "station",
            "channel",
            "arrival_epoch",
            "arrival_iso",
            "event_reference_epoch",
            "delta_seconds",
        ]
    ].copy()
    output = output.rename(columns={"event_index": "event_id"})

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output_csv, index=False)
    logger.info(f"Scritto {args.output_csv} ({len(output)} righe).")


if __name__ == "__main__":
    main()
