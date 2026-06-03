#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from utils import load_csv_with_checks, setup_logger

logger = setup_logger("compute_stats")


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Calcola statistiche per stazione a partire dai CSV dei delta "
            "(es. station_deltas_science.csv)."
        )
    )
    parser.add_argument("--base-csv", required=True, type=Path, help="CSV run base.")
    parser.add_argument("--soft-csv", type=Path, help="CSV run soft (opzionale).")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=project_root / "data" / "processed" / "station_delta_stats_science.csv",
        help="File di output con le statistiche aggregate.",
    )
    parser.add_argument(
        "--channel",
        help="Filtra su una singola component (es. HHZ); se omesso usa tutti i canali.",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=10,
        help="Esclude stazioni con meno di questa numerosità (default 10).",
    )
    parser.add_argument(
        "--stations-file",
        type=Path,
        help="File TXT opzionale con i codici stazione da mantenere (uno per riga).",
    )
    return parser.parse_args()


def load_delta_csv(path: Path, channel: Optional[str]) -> pd.DataFrame:
    df = load_csv_with_checks(path, {"station", "delta_seconds"})
    df = df.copy()
    if channel:
        if "channel" not in df.columns:
            raise SystemExit(f"{path} non contiene la colonna channel per filtrare.")
        df = df[df["channel"].astype(str).str.upper() == channel.upper()]
    df["station"] = df["station"].astype(str)
    return df


def summarize(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    grouped = (
        df.groupby("station")["delta_seconds"]
        .agg(
            count="count",
            mean="mean",
            std="std",
            median="median",
        )
        .reset_index()
    )
    grouped = grouped.rename(
        columns={
            "count": f"{prefix}_count",
            "mean": f"{prefix}_mean",
            "std": f"{prefix}_std",
            "median": f"{prefix}_median",
        }
    )
    grouped[f"{prefix}_std"] = grouped[f"{prefix}_std"].fillna(0.0)
    return grouped


def main() -> None:
    args = parse_args()

    base_df = load_delta_csv(args.base_csv, args.channel)
    if args.stations_file:
        if not args.stations_file.exists():
            raise SystemExit(f"File stazioni non trovato: {args.stations_file}")
        allowed_stations = {
            line.strip().upper()
            for line in args.stations_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if allowed_stations:
            base_df = base_df[base_df["station"].astype(str).str.upper().isin(allowed_stations)]
            if base_df.empty:
                raise SystemExit("Filtrando per stazioni non resta alcuna osservazione nel CSV base.")

    base_stats = summarize(base_df, "base")

    if args.soft_csv:
        soft_df = load_delta_csv(args.soft_csv, args.channel)
        if args.stations_file and not soft_df.empty:
            soft_df = soft_df[soft_df["station"].astype(str).str.upper().isin(allowed_stations)]
            if soft_df.empty:
                raise SystemExit("Filtrando per stazioni non resta alcuna osservazione nel CSV soft.")
        soft_stats = summarize(soft_df, "soft")
        stats = base_stats.merge(soft_stats, on="station", how="outer")
        stats["soft_minus_base_mean"] = stats["soft_mean"] - stats["base_mean"]
    else:
        stats = base_stats

    if args.min_count > 0:
        count_cols = [c for c in stats.columns if c.endswith("_count")]
        mask = np.zeros(len(stats), dtype=bool)
        for col in count_cols:
            mask |= stats[col].fillna(0) >= args.min_count
        stats = stats[mask]

    stats = stats.sort_values("station")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    stats.to_csv(args.output_csv, index=False)
    logger.info(f"Statistiche salvate in {args.output_csv} ({len(stats)} stazioni).")


if __name__ == "__main__":
    main()
