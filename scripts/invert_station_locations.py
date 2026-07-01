#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import pyproj
from scipy.optimize import least_squares
from utils import load_config, load_csv_with_checks, setup_logger

logger = setup_logger("invert_locations")


@dataclass
class EventPoint:
    x_m: float
    y_m: float
    z_m: float
    origin_epoch: float


def read_events(path: Path, epsg: int) -> pd.DataFrame:
    required = {"event_index", "LAT", "LON", "DEPTH", "TIME"}
    df = load_csv_with_checks(path, required)
    df = df[list(required)].copy()
    df = df.rename(
        columns={
            "LAT": "lat",
            "LON": "lon",
            "DEPTH": "depth_km",
            "TIME": "origin_time",
        },
    )
    df["event_index"] = df["event_index"].astype(int)
    time_values = df["origin_time"].astype(str)
    datetime_idx = time_values.str.contains(r"[+T:]", na=False)
    numeric_idx = ~datetime_idx

    origin_epoch = np.full(len(df), np.nan, dtype=float)

    if datetime_idx.any():
        dt_values = pd.to_datetime(time_values[datetime_idx], utc=True, errors="coerce")
        if dt_values.isna().any():
            bad_ids = (
                df.loc[datetime_idx][dt_values.isna()]["event_index"]
                .astype(str)
                .tolist()
            )
            raise SystemExit(
                f"TIME non parsabile per gli event_index: {', '.join(bad_ids)}"
            )
        origin_epoch[np.where(datetime_idx)[0]] = dt_values.astype("int64") / 1e9

    if numeric_idx.any():
        numeric_vals = pd.to_numeric(time_values[numeric_idx], errors="coerce")
        if numeric_vals.isna().any():
            bad_ids = (
                df.loc[numeric_idx][numeric_vals.isna()]["event_index"]
                .astype(str)
                .tolist()
            )
            raise SystemExit(
                f"TIME numerico non parsabile per gli event_index: {', '.join(bad_ids)}"
            )
        origin_epoch[np.where(numeric_idx)[0]] = numeric_vals.astype(float)

    if np.isnan(origin_epoch).any():
        raise SystemExit("Alcuni TIME non sono stati convertiti correttamente.")

    df["origin_epoch"] = origin_epoch

    transformer = pyproj.Transformer.from_crs(
        "EPSG:4326", f"EPSG:{epsg}", always_xy=True
    )
    x, y = transformer.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    df["x_m"] = x
    df["y_m"] = y
    # Converte la profondità in metri e la rende negativa (quota sotto la superficie).
    df["z_m"] = -df["depth_km"].astype(float) * 1000.0
    return df[["event_index", "x_m", "y_m", "z_m", "origin_epoch"]]


def read_picks(path: Path, phase_type: str) -> pd.DataFrame:
    required = {"station_id", "phase_time", "phase_type", "event_index"}
    df = load_csv_with_checks(path, required)
    df = df[list(required)].copy()
    df["phase_type"] = df["phase_type"].astype(str).str.upper()
    df = df[df["phase_type"] == phase_type.upper()]
    if df.empty:
        raise SystemExit(f"Nessuna pick con phase_type={phase_type!r} in {path}.")

    df["station"] = df["station_id"].astype(str).str.split(".").str[1].str.upper()
    df["phase_time"] = pd.to_datetime(df["phase_time"], utc=True, errors="coerce")
    df = df.dropna(subset=["phase_time"])
    df["arrival_epoch"] = df["phase_time"].astype("int64") / 1e9
    df["event_index"] = df["event_index"].astype(int)
    return df[["station", "event_index", "arrival_epoch"]]


def build_station_observations(
    events_df: pd.DataFrame,
    picks_df: pd.DataFrame,
    min_events: int,
    tmin: float,
    tmax: float,
) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    merged = picks_df.merge(events_df, on="event_index", how="inner")
    merged["travel_time"] = merged["arrival_epoch"] - merged["origin_epoch"]
    merged = merged[(merged["travel_time"] >= tmin) & (merged["travel_time"] <= tmax)]

    station_groups = {}
    grouped = merged.groupby("station")
    for station, group in grouped:
        if len(group) < min_events:
            continue
        event_points = group[["x_m", "y_m", "z_m"]].to_numpy(dtype=float)
        travel_times = group["travel_time"].to_numpy(dtype=float)
        station_groups[station] = (
            event_points,
            travel_times,
            group["event_index"].to_numpy(dtype=int),
        )
    return station_groups


def initial_guess(
    event_points: np.ndarray,
    travel_times: np.ndarray,
    guess_velocity: float,
    z_station: float,
) -> np.ndarray:
    # Stima iniziale come media dei punti evento proiettata in superficie.
    xy_mean = event_points[:, :2].mean(axis=0)
    return np.array([xy_mean[0], xy_mean[1], guess_velocity], dtype=float)


def residuals(
    params: np.ndarray,
    event_points: np.ndarray,
    travel_times: np.ndarray,
    z_station: float,
) -> np.ndarray:
    x, y, velocity = params
    if velocity <= 0.0:
        return np.full_like(travel_times, 1e6)
    dx = event_points[:, 0] - x
    dy = event_points[:, 1] - y
    dz = event_points[:, 2] - z_station
    distances = np.sqrt(dx**2 + dy**2 + dz**2)
    model_times = distances / velocity
    return model_times - travel_times


def invert_station(
    station: str,
    event_points: np.ndarray,
    travel_times: np.ndarray,
    z_station: float,
    guess_velocity: float,
    xy_bounds: tuple[float, float, float, float],
    v_bounds: tuple[float, float],
) -> Optional[dict[str, float]]:
    x_min, x_max, y_min, y_max = xy_bounds
    v_min, v_max = v_bounds

    x0 = initial_guess(event_points, travel_times, guess_velocity, z_station)
    bounds_lower = np.array([x_min, y_min, v_min], dtype=float)
    bounds_upper = np.array([x_max, y_max, v_max], dtype=float)

    result = least_squares(
        residuals,
        x0=x0,
        bounds=(bounds_lower, bounds_upper),
        args=(event_points, travel_times, z_station),
    )
    if not result.success:
        return None
    x_opt, y_opt, v_opt = result.x
    res = residuals(result.x, event_points, travel_times, z_station)
    rms = float(np.sqrt(np.mean(res**2)))
    return {
        "station": station,
        "x_m": float(x_opt),
        "y_m": float(y_opt),
        "velocity_m_s": float(v_opt),
        "velocity_km_s": float(v_opt / 1000.0),
        "n_events": int(len(travel_times)),
        "rms_seconds": rms,
        "success": bool(result.success),
    }


def parse_args() -> argparse.Namespace:
    config = load_config()
    inv_cfg = config.get("inversion", {})
    geo_cfg = config.get("geospatial", {})

    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Ricostruisce la posizione delle stazioni ottimizzando la distanza evento-stazione "
            "in base ai tempi di arrivo."
        )
    )
    parser.add_argument(
        "--events-csv", required=True, type=Path, help="File science_s1 (eventi)."
    )
    parser.add_argument(
        "--picks-csv",
        required=True,
        type=Path,
        help="File science_s2 (pick delle fasi).",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=project_root / "data" / "processed" / "station_locations_inverted.csv",
    )
    parser.add_argument(
        "--phase-type", default="P", help="Tipo di fase da usare (default: P)."
    )
    parser.add_argument(
        "--epsg",
        type=int,
        default=geo_cfg.get("epsg", 32633),
        help="Sistema di proiezione metrico.",
    )
    parser.add_argument(
        "--guess-velocity",
        type=float,
        default=inv_cfg.get("guess_velocity_m_s", 4000.0),
        help="Velocità iniziale in m/s.",
    )
    parser.add_argument(
        "--velocity-min",
        type=float,
        default=inv_cfg.get("velocity_min_m_s", 1500.0),
        help="Limite inferiore velocità (m/s).",
    )
    parser.add_argument(
        "--velocity-max",
        type=float,
        default=inv_cfg.get("velocity_max_m_s", 7000.0),
        help="Limite superiore velocità (m/s).",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=inv_cfg.get("min_events_per_station", 8),
        help="Numero minimo di eventi per stazione.",
    )
    parser.add_argument(
        "--travel-time-min",
        type=float,
        default=inv_cfg.get("travel_time_min_s", 0.05),
        help="Tempo minimo (s) per evitare tempi nulli o negativi (default 0.05).",
    )
    parser.add_argument(
        "--travel-time-max",
        type=float,
        default=inv_cfg.get("travel_time_max_s", 120.0),
        help="Tempo massimo (s) per scartare outlier molto distanti (default 120s).",
    )
    parser.add_argument(
        "--surface-elevation",
        type=float,
        default=geo_cfg.get("surface_elevation_m", 0.0),
        help="Quota stimata della stazione (m). Default 0 (livello medio del mare).",
    )
    parser.add_argument(
        "--margin-km",
        type=float,
        default=inv_cfg.get("margin_km", 15.0),
        help="Margine aggiunto al bounding box degli eventi per i limiti dell'inversione (km).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    events_df = read_events(args.events_csv, args.epsg)
    picks_df = read_picks(args.picks_csv, args.phase_type)
    stations_data = build_station_observations(
        events_df,
        picks_df,
        min_events=args.min_events,
        tmin=args.travel_time_min,
        tmax=args.travel_time_max,
    )
    if not stations_data:
        raise SystemExit("Nessuna stazione con numero sufficiente di eventi.")

    x_min = events_df["x_m"].min()
    x_max = events_df["x_m"].max()
    y_min = events_df["y_m"].min()
    y_max = events_df["y_m"].max()
    margin = max(args.margin_km, 0.0) * 1000.0
    xy_bounds = (x_min - margin, x_max + margin, y_min - margin, y_max + margin)
    v_bounds = (args.velocity_min, args.velocity_max)

    results: list[dict[str, float]] = []
    failures: list[str] = []
    for station, (event_points, travel_times, event_ids) in sorted(
        stations_data.items()
    ):
        outcome = invert_station(
            station=station,
            event_points=event_points,
            travel_times=travel_times,
            z_station=args.surface_elevation,
            guess_velocity=args.guess_velocity,
            xy_bounds=xy_bounds,
            v_bounds=v_bounds,
        )
        if outcome is None:
            failures.append(station)
            continue
        outcome["event_ids_used"] = ",".join(str(eid) for eid in np.unique(event_ids))
        results.append(outcome)

    if not results:
        raise SystemExit(
            "Inversione fallita per tutte le stazioni. Controlla parametri e dati."
        )

    out_df = pd.DataFrame(results)

    # Converte x/y back to lat/lon.
    transformer = pyproj.Transformer.from_crs(
        f"EPSG:{args.epsg}", "EPSG:4326", always_xy=True
    )
    lon, lat = transformer.transform(out_df["x_m"].to_numpy(), out_df["y_m"].to_numpy())
    out_df["latitude"] = lat
    out_df["longitude"] = lon
    out_df["elevation_assumed_m"] = args.surface_elevation

    cols_order = [
        "station",
        "latitude",
        "longitude",
        "elevation_assumed_m",
        "x_m",
        "y_m",
        "velocity_m_s",
        "velocity_km_s",
        "n_events",
        "rms_seconds",
        "event_ids_used",
        "success",
    ]
    out_df = out_df[cols_order]
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.output_csv, index=False)
    logger.info(
        f"Inversione completata per {len(out_df)} stazioni. Risultati in {args.output_csv}"
    )

    if failures:
        logger.warning(
            f"Stazioni senza soluzione convergente: {', '.join(sorted(failures))}"
        )


if __name__ == "__main__":
    main()
