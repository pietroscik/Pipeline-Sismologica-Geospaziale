#!/usr/bin/env python3
"""
Seleziona le stazioni sismiche in base a criteri spaziali (punto focale + raggio, oppure poligono).
Esporta un file di testo con i codici stazione da usare nella pipeline.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from utils import load_csv_with_checks, setup_logger, load_config, get_project_root

logger = setup_logger("spatial_select")


def parse_args() -> argparse.Namespace:
    config = load_config()
    geo_cfg = config.get("geospatial", {})
    project_root = get_project_root()

    parser = argparse.ArgumentParser(description="Filtra le stazioni sismiche per coordinate focali o poligono.")
    parser.add_argument("--input-csv", type=Path, default=project_root / "data" / "raw" / "stations.csv", help="CSV stazioni completo.")
    parser.add_argument("--output-file", type=Path, default=project_root / "data" / "raw" / "selected_stations.txt", help="File TXT in output con le stazioni filtrate.")
    parser.add_argument("--point", nargs=3, metavar=("LAT", "LON", "RADIUS_KM"), type=float, help="Seleziona stazioni entro RADIUS_KM da LAT e LON specificati.")
    parser.add_argument("--polygon", type=str, help="Seleziona stazioni in un poligono. Formato: 'lat1,lon1 lat2,lon2 lat3,lon3 ...'")
    parser.add_argument("--epsg", type=int, default=geo_cfg.get("epsg", 32633), help="EPSG metrico da usare per calcolare il buffer/raggio.")
    
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    if not args.point and not args.polygon:
        logger.error("Devi specificare almeno un criterio di selezione: --point oppure --polygon.")
        raise SystemExit(1)
        
    df = load_csv_with_checks(args.input_csv, {"station", "latitude", "longitude"})
    
    # Creiamo il GeoDataFrame delle stazioni (EPSG:4326 geografico nativo)
    gdf_stations = gpd.GeoDataFrame(
        df, 
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326"
    )
    
    # Proiettiamo nel sistema metrico per calcoli di distanza precisi (es. UTM)
    gdf_stations_metric = gdf_stations.to_crs(f"EPSG:{args.epsg}")
    
    if args.point:
        lat, lon, radius_km = args.point
        focal_point = Point(lon, lat)
        focal_gdf = gpd.GeoDataFrame(geometry=[focal_point], crs="EPSG:4326").to_crs(f"EPSG:{args.epsg}")
        buffer_metric = focal_gdf.geometry.buffer(radius_km * 1000)  # Raggio convertito in metri
        
        selected = gdf_stations_metric[gdf_stations_metric.geometry.intersects(buffer_metric.iloc[0])]
        logger.info(f"Filtro a Punto: LAT {lat}, LON {lon} con raggio {radius_km} km. Trovate {len(selected)} stazioni.")
        
    elif args.polygon:
        coords = []
        for pt in args.polygon.split():
            lat_str, lon_str = pt.split(",")
            coords.append((float(lon_str), float(lat_str)))  # Shapely usa (x, y) -> (lon, lat)
        
        poly = Polygon(coords)
        poly_gdf = gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326").to_crs(f"EPSG:{args.epsg}")
        
        selected = gdf_stations_metric[gdf_stations_metric.geometry.intersects(poly_gdf.geometry.iloc[0])]
        logger.info(f"Filtro a Poligono: Trovate {len(selected)} stazioni.")
            
    station_codes = selected["station"].unique().tolist()
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text("\n".join(station_codes) + "\n", encoding="utf-8")
    logger.info(f"Codici stazioni esportati con successo in: {args.output_file}")

if __name__ == "__main__":
    main()