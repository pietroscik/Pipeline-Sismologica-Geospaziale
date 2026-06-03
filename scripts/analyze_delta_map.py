#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Tuple

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_bounds
from scipy.interpolate import griddata

from utils import load_csv_with_checks, setup_logger, load_config, get_project_root

logger = setup_logger("analyze_map")


def load_delta_csv(path: Path) -> pd.DataFrame:
    return load_csv_with_checks(path, {"x_m", "y_m", "delta_seconds"})


def plot_scatter(df: pd.DataFrame, outdir: Optional[Path], title_suffix: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    sc = ax.scatter(
        df["x_m"], df["y_m"], c=df["delta_seconds"],
        cmap="RdBu_r", s=10, edgecolors="none"
    )
    plt.colorbar(sc, ax=ax, label="Delta (s)")
    ax.set_title(f"Scatter delta {title_suffix}")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    if outdir:
        out_path = outdir / f"delta_scatter_{title_suffix}.png"
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_interpolated(
    df: pd.DataFrame,
    outdir: Optional[Path],
    title_suffix: str,
    grid_points: int,
    contour_levels: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = df["x_m"].values
    y = df["y_m"].values
    z = df["delta_seconds"].values

    grid_x, grid_y = np.mgrid[
        x.min():x.max():complex(0, grid_points),
        y.min():y.max():complex(0, grid_points),
    ]
    grid_z = griddata((x, y), z, (grid_x, grid_y), method="cubic")

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(
        grid_z.T,
        extent=(x.min(), x.max(), y.min(), y.max()),
        origin="lower",
        cmap="RdBu_r",
        aspect="equal",
    )
    plt.colorbar(im, ax=ax, label="Delta (s)")
    ax.set_title(f"Interpolazione delta {title_suffix}")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")

    if contour_levels > 0:
        levels = np.linspace(np.nanmin(grid_z), np.nanmax(grid_z), contour_levels)
        cs = ax.contour(
            grid_x, grid_y, grid_z, levels=levels, colors="k", linewidths=0.5
        )
        ax.clabel(cs, fmt="%.1f", fontsize=7)

    fig.tight_layout()
    if outdir:
        out_path = outdir / f"delta_interpolated_{title_suffix}.png"
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)
    return grid_x, grid_y, grid_z


def plot_station_stats(path: Path, outdir: Optional[Path]) -> None:
    df = pd.read_csv(path)
    if "station" not in df.columns or "soft_mean" not in df.columns:
        logger.warning(f"File {path} senza colonne attese; salto plot statistiche.")
        return

    df_sorted = df.sort_values("soft_mean")

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(df_sorted["station"], df_sorted["soft_mean"])
    ax.axhline(0.0, color="k", linewidth=0.8)
    ax.set_title("Media delta (run soft) per stazione")
    ax.set_xlabel("Stazione")
    ax.set_ylabel("Soft mean delta (s)")
    fig.tight_layout()
    if outdir:
        out_path = outdir / "station_soft_mean.png"
        fig.savefig(out_path, dpi=180)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


def write_geotiff(
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    grid_z: np.ndarray,
    out_path: Path,
    nodata: float = -9999.0,
    epsg: int = 32633,
) -> None:
    xmin, xmax = grid_x.min(), grid_x.max()
    ymin, ymax = grid_y.min(), grid_y.max()
    height, width = grid_z.shape
    transform = from_bounds(xmin, ymin, xmax, ymax, width, height)
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=f"EPSG:{epsg}",
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(np.nan_to_num(grid_z, nan=nodata).T.astype("float32"), 1)
    logger.info(f"GeoTIFF salvato in {out_path}")


def export_shapefile(df: pd.DataFrame, out_path: Path, epsg: int = 32633) -> None:
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["x_m"], df["y_m"]),
        crs=f"EPSG:{epsg}",
    )
    gdf.to_file(out_path)
    logger.info(f"Shapefile salvato in {out_path}")


def plot_anomaly_clusters(
    df: pd.DataFrame,
    outdir: Optional[Path],
    threshold: float,
    title_suffix: str,
    epsg: int = 32633,
) -> None:
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["x_m"], df["y_m"]),
        crs=f"EPSG:{epsg}",
    )
    slow = gdf[gdf["delta_seconds"] >= threshold]
    fast = gdf[gdf["delta_seconds"] <= -threshold]

    fig, ax = plt.subplots(figsize=(8, 6))
    gdf.plot(ax=ax, color="lightgrey", markersize=2, label="Area")
    if not slow.empty:
        slow.plot(ax=ax, color="red", markersize=5, label=f">= {threshold}s")
    if not fast.empty:
        fast.plot(ax=ax, color="blue", markersize=5, label=f"<= -{threshold}s")

    ax.set_title(f"Zone anomale delta {title_suffix}")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal", adjustable="box")
    ax.legend()
    fig.tight_layout()

    if outdir:
        out_path = outdir / f"delta_anomaly_{title_suffix}.png"
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


def resolve_outdir(outdir: Optional[Path]) -> Optional[Path]:
    """Resolve outdir against project root if it's a relative path."""
    if outdir is None:
        return None
    if not outdir.is_absolute():
        return get_project_root() / outdir
    return outdir


def main() -> None:
    config = load_config()
    map_cfg = config.get("mapping", {})
    geo_cfg = config.get("geospatial", {})

    parser = argparse.ArgumentParser(
        description="Analisi mappa dei delta (scatter, interpolazione, shapefile, GeoTIFF)."
    )
    parser.add_argument(
        "--delta-csv", required=True, type=Path,
        help="CSV con colonne x_m, y_m, delta_seconds (es. delta_surface_soft.csv).",
    )
    parser.add_argument(
        "--stats-csv", type=Path,
        help="CSV con statistiche per stazione (es. station_delta_stats.csv).",
    )
    parser.add_argument(
        "--outdir", type=Path,
        help="Cartella di output per immagini/raster/shapefile.",
    )
    parser.add_argument(
        "--grid-points", type=int, default=map_cfg.get("grid_points", 400),
        help="Risoluzione griglia per l'interpolazione.",
    )
    parser.add_argument(
        "--contour-levels", type=int, default=map_cfg.get("contour_levels", 8),
        help="Numero di livelli di contorno (0 per disabilitare).",
    )
    parser.add_argument(
        "--title-suffix", default="soft",
        help="Suffisso per titoli e nomi file.",
    )
    parser.add_argument(
        "--export-geotiff", action="store_true",
        help="Se impostato, esporta una GeoTIFF dell'interpolazione.",
    )
    parser.add_argument(
        "--export-shapefile", action="store_true",
        help="Se impostato, esporta uno shapefile dei punti.",
    )
    parser.add_argument(
        "--anomaly-threshold", type=float, default=map_cfg.get("default_anomaly_threshold", 0.5),
        help="Se impostato, crea un plot evidenziando punti con |delta| >= soglia."
    )
    parser.add_argument(
        "--epsg", type=int, default=geo_cfg.get("epsg", 32633),
        help="Sistema di proiezione metrico (es. 32633)."
    )
    args = parser.parse_args()

    df = load_delta_csv(args.delta_csv)

    outdir = resolve_outdir(args.outdir)
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    plot_scatter(df, outdir, args.title_suffix)
    grid_x, grid_y, grid_z = plot_interpolated(
        df, outdir, args.title_suffix,
        grid_points=args.grid_points,
        contour_levels=args.contour_levels,
    )

    if args.export_geotiff:
        if not outdir:
            raise SystemExit("Per esportare GeoTIFF è necessario specificare --outdir")
        geotiff_path = outdir / f"delta_{args.title_suffix}.tif"
        write_geotiff(grid_x, grid_y, grid_z, geotiff_path, epsg=args.epsg)

    if args.export_shapefile:
        if not outdir:
            raise SystemExit("Per esportare shapefile è necessario specificare --outdir")
        shapefile_path = outdir / f"delta_points_{args.title_suffix}.shp"
        export_shapefile(df, shapefile_path, epsg=args.epsg)

    if args.anomaly_threshold is not None:
        plot_anomaly_clusters(df, outdir, args.anomaly_threshold, args.title_suffix, epsg=args.epsg)

    if args.stats_csv:
        plot_station_stats(args.stats_csv, outdir)

    logger.info("Analisi completata.")


if __name__ == "__main__":
    main()