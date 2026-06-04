#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Aggiungiamo la root del progetto al sys.path per garantire importazioni assolute e globali
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from obspy.clients.fdsn import Client

from scripts.utils import setup_logger

logger = setup_logger("export_missing")


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Recupera le coordinate delle stazioni mancanti nel CSV delle stazioni."
    )
    parser.add_argument(
        "--delta-csv",
        required=True,
        type=Path,
        help="CSV con colonne station o station_id (es. station_deltas_science.csv o science.adw9038_data_s2.csv).",
    )
    parser.add_argument(
        "--stations-csv",
        required=True,
        type=Path,
        help="CSV con station, latitude, longitude, elevation.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=project_root / "data" / "interim" / "stations_missing.csv",
        help="Dove salvare le coordinate mancanti.",
    )
    parser.add_argument(
        "--network",
        default="IV",
        help="Rete FDSN da interrogare (default: IV).",
    )
    return parser.parse_args()


def collect_station_codes(delta_path: Path) -> pd.Series:
    df = pd.read_csv(delta_path)
    if "station" in df.columns:
        stations = df["station"]
    elif "station_id" in df.columns:
        stations = df["station_id"].astype(str).str.split(".").str[1]
    else:
        raise SystemExit("Il file delta deve contenere la colonna 'station' oppure 'station_id'.")
    return stations.dropna().astype(str).str.strip().str.upper().drop_duplicates()


def load_known_stations(stations_path: Path) -> pd.Series:
    df = pd.read_csv(stations_path)
    if "station" not in df.columns:
        raise SystemExit(f"{stations_path} privo della colonna 'station'.")
    return df["station"].astype(str).str.strip().str.upper()


def main() -> None:
    args = parse_args()

    delta_stations = collect_station_codes(args.delta_csv)
    known_stations = load_known_stations(args.stations_csv)

    missing = sorted(set(delta_stations) - set(known_stations))
    if not missing:
        logger.info("Nessuna stazione mancante: file aggiornato.")
        return

    logger.info(f"Recupero coordinate per {len(missing)} stazioni: {', '.join(missing)}")
    
    try:
        client = Client("INGV", timeout=30)
    except Exception as exc:
        raise SystemExit(f"Impossibile inizializzare il client FDSN. Rete non disponibile? Errore: {exc}")
        
    rows = []
    failures = []
    for code in missing:
        try:
            inventory = client.get_stations(network=args.network, station=code, level="station")
        except Exception as exc:  # noqa: BLE001
            failures.append((code, str(exc)))
            continue
        if inventory is None or len(inventory) == 0:
            failures.append((code, "Nessun dato restituito dal servizio"))
            continue
        for network in inventory:
            for station in network:
                rows.append(
                    dict(
                        network=network.code,
                        station=station.code,
                        latitude=station.latitude,
                        longitude=station.longitude,
                        elevation=station.elevation,
                    )
                )

    if not rows:
        msg = "Nessuna coordinata recuperata; controlla i codici o la rete."
        if failures:
            details = "; ".join(f"{code}: {reason}" for code, reason in failures)
            msg += f" Dettagli: {details}"
        raise SystemExit(msg)

    out_df = pd.DataFrame(rows).drop_duplicates("station").sort_values("station")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.output_csv, index=False)
    logger.info(f"Coordinate mancanti salvate in {args.output_csv}")

    if failures:
        logger.warning("Attenzione, alcune stazioni non sono state trovate:")
        for code, reason in failures:
            logger.warning(f"  - {code}: {reason}")


if __name__ == "__main__":
    main()
