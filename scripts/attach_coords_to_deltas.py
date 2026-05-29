#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pyproj

from utils import load_csv_with_checks, setup_logger, load_config, get_project_root

logger = setup_logger("attach_coords")


def parse_args() -> argparse.Namespace:
    config = load_config()
    geo_cfg = config.get("geospatial", {})

    project_root = get_project_root()
    parser = argparse.ArgumentParser(
        description="Aggiunge coordinate metriche ai delta per stazione unendo il CSV delle stazioni."
    )
    parser.add_argument("--delta-csv", required=True, type=Path, help="CSV con colonna station e valori delta/statistici.")
    parser.add_argument("--stations-csv", required=True, type=Path, help="CSV con station, latitude, longitude.")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=project_root / "data" / "processed" / "station_deltas_with_coords.csv",
        help="Percorso del CSV arricchito.",
    )
    parser.add_argument("--epsg", type=int, default=geo_cfg.get("epsg", 32633), help="EPSG per la proiezione metrico.")
    parser.add_argument(
        "--value-column",
        help=(
            "Colonna da usare come delta_seconds. Se omessa usa delta_seconds, "
            "oppure base_mean/soft_mean quando riceve statistiche per stazione."
        ),
    )
    return parser.parse_args()


def normalize_delta_column(df: pd.DataFrame, requested_column: str | None) -> pd.DataFrame:
    candidates = ["delta_seconds", "base_mean", "soft_mean", "base_median", "soft_median"]
    if requested_column:
        if requested_column not in df.columns:
            raise SystemExit(f"Colonna richiesta non presente in delta CSV: {requested_column}")
        source_column = requested_column
    else:
        source_column = next((column for column in candidates if column in df.columns), None)
        if source_column is None:
            available = ", ".join(df.columns)
            raise SystemExit(
                "Il CSV delta deve contenere una colonna valore tra "
                f"{', '.join(candidates)}. Colonne disponibili: {available}"
            )

    values = pd.to_numeric(df[source_column], errors="coerce")
    if values.isna().any():
        raise SystemExit(f"La colonna {source_column} contiene valori delta non numerici o mancanti.")

    if source_column != "delta_seconds":
        logger.info(f"Uso {source_column} come delta_seconds per la spazializzazione.")
    df = df.copy()
    df["delta_seconds"] = values
    return df


def main() -> None:
    args = parse_args()

    delta_df = load_csv_with_checks(args.delta_csv, {"station"}).copy()
    delta_df = normalize_delta_column(delta_df, args.value_column)
    station_df = load_csv_with_checks(args.stations_csv, {"station", "latitude", "longitude"}).copy()

    delta_df["station"] = delta_df["station"].astype(str).str.strip().str.upper()
    station_df["station"] = station_df["station"].astype(str).str.strip().str.upper()

    merged = delta_df.merge(station_df, on="station", how="left")
    missing = merged["latitude"].isna() | merged["longitude"].isna()
    if missing.any():
        missing_codes = sorted({str(code) for code in merged.loc[missing, "station"]})
        raise SystemExit(f"Coordinate mancanti per le stazioni: {', '.join(missing_codes)}")

    transformer = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{args.epsg}", always_xy=True)
    x, y = transformer.transform(merged["longitude"].to_numpy(), merged["latitude"].to_numpy())
    merged["x_m"] = x
    merged["y_m"] = y

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.output_csv, index=False)
    logger.info(f"CSV arricchito salvato in {args.output_csv} ({len(merged)} righe).")


if __name__ == "__main__":
    main()
