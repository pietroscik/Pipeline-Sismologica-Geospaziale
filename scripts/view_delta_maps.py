#!/usr/bin/env python3
"""
Utility per visualizzare e confrontare i delta (run base/soft) e creare
mappe raster con basemap e confini amministrativi.

Requisiti:
    pip install pandas numpy matplotlib geopandas rasterio scipy contextily
"""

from __future__ import annotations

import argparse
from pathlib import Path

import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy.interpolate import griddata
from utils import (get_project_root, load_config, load_csv_with_checks,
                   setup_logger)

logger = setup_logger("view_maps")


# ---------------------------------------------------------------------------
# Lettura CSV e mappe base
# ---------------------------------------------------------------------------


def read_delta_csv(path: Path) -> pd.DataFrame:
    return load_csv_with_checks(path, {"x_m", "y_m", "delta_seconds"})


def scatter_map(df: pd.DataFrame, title: str, out_path: Path | None) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    sc = ax.scatter(
        df["x_m"],
        df["y_m"],
        c=df["delta_seconds"],
        cmap="RdBu_r",
        s=10,
        edgecolors="none",
    )
    plt.colorbar(sc, ax=ax, label="Delta (s)")
    ax.set_title(title)
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


def interpolated_map(
    df: pd.DataFrame,
    title: str,
    out_path: Path | None,
    grid_points: int = 400,
    contour_levels: int = 8,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = df["x_m"].values
    y = df["y_m"].values
    z = df["delta_seconds"].values

    grid_x, grid_y = np.mgrid[
        x.min() : x.max() : complex(grid_points),
        y.min() : y.max() : complex(grid_points),
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
    ax.set_title(title)
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")

    if contour_levels > 0:
        levels = np.linspace(np.nanmin(grid_z), np.nanmax(grid_z), contour_levels)
        cs = ax.contour(
            grid_x, grid_y, grid_z, levels=levels, colors="k", linewidths=0.5
        )
        ax.clabel(cs, fmt="%.1f", fontsize=7)

    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)
    return grid_x, grid_y, grid_z


def stats_bar(stats_csv: Path, out_path: Path | None) -> None:
    df = pd.read_csv(stats_csv)
    if "station" not in df.columns or "soft_mean" not in df.columns:
        logger.warning(f"{stats_csv} privo di colonne attese, salto bar chart.")
        return

    df_sorted = df.sort_values("soft_mean")
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(df_sorted["station"], df_sorted["soft_mean"])
    ax.axhline(0.0, color="k", linewidth=0.8)
    ax.set_title("Media delta (run soft) per stazione")
    ax.set_xlabel("Stazione")
    ax.set_ylabel("Soft mean delta (s)")
    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# Raster e GeoTIFF
# ---------------------------------------------------------------------------


def reproject_raster(
    src_path: Path, dst_crs: str = "EPSG:3857"
) -> tuple[np.ndarray, rasterio.Affine]:
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update(
            {"crs": dst_crs, "transform": transform, "width": width, "height": height}
        )
        data = np.empty((src.count, height, width), dtype=np.float32)
        for i in range(1, src.count + 1):
            reproject(
                source=rasterio.band(src, i),
                destination=data[i - 1],
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
    return data[0], transform


def raster_with_basemap(
    raster_path: Path,
    points_df: pd.DataFrame,
    title: str,
    out_path: Path | None,
    anomaly_threshold: float | None = None,
    boundaries_path: Path | None = None,
    focus_margin_km: float | None = None,
    epsg: int = 32633,
) -> None:
    raster_data, transform = reproject_raster(raster_path, "EPSG:3857")
    bounds = array_bounds(raster_data.shape[0], raster_data.shape[1], transform)
    extent = [bounds[0], bounds[2], bounds[1], bounds[3]]

    gdf_points = gpd.GeoDataFrame(
        points_df,
        geometry=gpd.points_from_xy(points_df["x_m"], points_df["y_m"]),
        crs=f"EPSG:{epsg}",
    ).to_crs("EPSG:3857")

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(
        raster_data,
        cmap="RdBu_r",
        extent=extent,
        origin="upper",
        alpha=0.7,
    )
    plt.colorbar(im, ax=ax, label="Delta (s)")

    if boundaries_path:
        boundaries = gpd.read_file(boundaries_path).to_crs("EPSG:3857")
        boundaries.boundary.plot(ax=ax, color="k", linewidth=0.8, label="Confini")

    if anomaly_threshold is not None:
        slow = gdf_points[gdf_points["delta_seconds"] >= anomaly_threshold]
        fast = gdf_points[gdf_points["delta_seconds"] <= -anomaly_threshold]
        if not slow.empty:
            slow.plot(
                ax=ax,
                color="red",
                markersize=5,
                marker="o",
                label=f">= {anomaly_threshold}s",
            )
        if not fast.empty:
            fast.plot(
                ax=ax,
                color="blue",
                markersize=5,
                marker="o",
                label=f"<= -{anomaly_threshold}s",
            )
    else:
        gdf_points.plot(ax=ax, color="black", markersize=3, alpha=0.5, label="Stazioni")

    if focus_margin_km is not None:
        margin = max(focus_margin_km * 1000.0, 0.0)
        pts_bounds = gdf_points.total_bounds
        if np.isfinite(pts_bounds).all():
            xmin = pts_bounds[0] - margin
            ymin = pts_bounds[1] - margin
            xmax = pts_bounds[2] + margin
            ymax = pts_bounds[3] + margin
            ax.set_xlim(xmin, xmax)
            ax.set_ylim(ymin, ymax)

    ctx.add_basemap(ax, crs="EPSG:3857", source=ctx.providers.OpenStreetMap.Mapnik)

    ax.set_title(title)
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.legend(loc="lower left")
    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=220)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


def difference_map(
    base_df: pd.DataFrame, soft_df: pd.DataFrame, out_path: Path | None
) -> None:
    merged = soft_df.merge(
        base_df, on=["x_m", "y_m"], how="inner", suffixes=("_soft", "_base")
    )
    merged["delta_diff"] = merged["delta_seconds_soft"] - merged["delta_seconds_base"]
    fig, ax = plt.subplots(figsize=(8, 6))
    sc = ax.scatter(
        merged["x_m"],
        merged["y_m"],
        c=merged["delta_diff"],
        cmap="coolwarm",
        s=10,
        edgecolors="none",
    )
    plt.colorbar(sc, ax=ax, label="Soft - Base (s)")
    ax.set_title("Differenza delta (soft - base)")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=200)
        logger.info(f"Salvato {out_path}")
    else:
        plt.show()
    plt.close(fig)


def write_diff_geotiff(
    base_grid: tuple[np.ndarray, np.ndarray, np.ndarray],
    soft_grid: tuple[np.ndarray, np.ndarray, np.ndarray],
    out_path: Path,
    epsg: int = 32633,
) -> None:
    _, _, base_z = base_grid
    _, _, soft_z = soft_grid
    if base_z.shape != soft_z.shape:
        raise ValueError("Le griglie base e soft devono avere la stessa dimensione.")
    diff = soft_z - base_z
    xmin = min(base_grid[0].min(), soft_grid[0].min())
    xmax = max(base_grid[0].max(), soft_grid[0].max())
    ymin = min(base_grid[1].min(), soft_grid[1].min())
    ymax = max(base_grid[1].max(), soft_grid[1].max())
    height, width = diff.shape
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
    ) as dst:
        dst.write(np.nan_to_num(diff, nan=-9999).astype("float32"), 1)
    logger.info(f"GeoTIFF differenza salvato in {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main() -> None:
    config = load_config()
    map_cfg = config.get("mapping", {})
    geo_cfg = config.get("geospatial", {})
    project_root = get_project_root()

    parser = argparse.ArgumentParser(
        description="Visualizza run base/soft, differenza e mappe raster con basemap."
    )
    parser.add_argument(
        "--base-csv", required=True, type=Path, help="delta_surface.csv run base"
    )
    parser.add_argument(
        "--soft-csv", required=True, type=Path, help="delta_surface_soft.csv run soft"
    )
    parser.add_argument(
        "--base-raster", required=True, type=Path, help="GeoTIFF interpolato base"
    )
    parser.add_argument(
        "--soft-raster", required=True, type=Path, help="GeoTIFF interpolato soft"
    )
    parser.add_argument(
        "--stats-csv", type=Path, help="station_delta_stats.csv per grafico stazioni"
    )
    parser.add_argument(
        "--outdir", type=Path, default=project_root / "results" / "maps"
    )
    parser.add_argument(
        "--grid-points", type=int, default=map_cfg.get("grid_points", 400)
    )
    parser.add_argument(
        "--contour-levels", type=int, default=map_cfg.get("contour_levels", 8)
    )
    parser.add_argument(
        "--anomaly-threshold",
        type=float,
        default=map_cfg.get("default_anomaly_threshold", 2.5),
    )
    parser.add_argument(
        "--boundaries", type=Path, help="Shapefile/GeoJSON dei confini amministrativi"
    )
    parser.add_argument(
        "--focus-margin-km",
        type=float,
        help="Se impostato, limita la vista basemap alla bounding box delle stazioni con il margine indicato (km).",
    )
    parser.add_argument(
        "--epsg",
        type=int,
        default=geo_cfg.get("epsg", 32633),
        help="Sistema di proiezione metrico.",
    )
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    df_base = read_delta_csv(args.base_csv)
    df_soft = read_delta_csv(args.soft_csv)

    scatter_map(df_base, "Delta scatter (base)", args.outdir / "scatter_base.png")
    scatter_map(df_soft, "Delta scatter (soft)", args.outdir / "scatter_soft.png")
    difference_map(df_base, df_soft, args.outdir / "scatter_difference.png")

    base_grid = interpolated_map(
        df_base,
        "Interpolazione delta (base)",
        args.outdir / "interpolated_base.png",
        grid_points=args.grid_points,
        contour_levels=args.contour_levels,
    )
    soft_grid = interpolated_map(
        df_soft,
        "Interpolazione delta (soft)",
        args.outdir / "interpolated_soft.png",
        grid_points=args.grid_points,
        contour_levels=args.contour_levels,
    )

    write_diff_geotiff(
        base_grid, soft_grid, args.outdir / "delta_diff.tif", epsg=args.epsg
    )

    if args.stats_csv:
        stats_bar(args.stats_csv, args.outdir / "station_soft_mean.png")

    raster_with_basemap(
        args.soft_raster,
        df_soft,
        "Delta interpolato (soft) con terreno e confini",
        args.outdir / "soft_raster_basemap.png",
        anomaly_threshold=args.anomaly_threshold,
        boundaries_path=args.boundaries,
        focus_margin_km=args.focus_margin_km,
        epsg=args.epsg,
    )
    raster_with_basemap(
        args.base_raster,
        df_base,
        "Delta interpolato (base) con terreno e confini",
        args.outdir / "base_raster_basemap.png",
        anomaly_threshold=args.anomaly_threshold,
        boundaries_path=args.boundaries,
        focus_margin_km=args.focus_margin_km,
        epsg=args.epsg,
    )

    logger.info(f"Output salvati in {args.outdir.resolve()}")


if __name__ == "__main__":
    main()
