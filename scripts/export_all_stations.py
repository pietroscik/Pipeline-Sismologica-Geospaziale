#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

from obspy import read_inventory
from utils import get_project_root


STATIONXML_PATH = get_project_root() / "CampiFlegrei_StationXML.xml"
OUTPUT_CSV_PATH = get_project_root() / "stations.csv"


def main() -> None:
    inventory = read_inventory(str(STATIONXML_PATH), format="STATIONXML")
    with OUTPUT_CSV_PATH.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["network", "station", "latitude", "longitude", "elevation"])
        for network in inventory:
            for station in network:
                writer.writerow(
                    [
                        network.code,
                        station.code,
                        station.latitude,
                        station.longitude,
                        station.elevation,
                    ]
                )
    print(f"Coordinate salvate in {OUTPUT_CSV_PATH}")


if __name__ == "__main__":
    main()