#!/usr/bin/env python
from __future__ import annotations

import csv
from pathlib import Path

from obspy.clients.fdsn import Client
from obspy import read_inventory
from utils import get_project_root


NETWORK = "IV"
STATIONS = ["CFB1", "CFB2", "CFB3", "CFSB", "POPT"]
STATIONXML_PATH = get_project_root() / "CampiFlegrei_StationXML.xml"
STATION_CSV_PATH = get_project_root() / "stations.csv"


def fetch_stationxml() -> None:
    client = Client("INGV")
    inventory = client.get_stations(
        network=NETWORK,
        station=",".join(STATIONS),
        level="station",
    )
    inventory.write(str(STATIONXML_PATH), format="STATIONXML")
    print(f"StationXML salvato in {STATIONXML_PATH}")


def stationxml_to_csv() -> None:
    inventory = read_inventory(str(STATIONXML_PATH), format="STATIONXML")
    with STATION_CSV_PATH.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["station", "latitude", "longitude", "elevation"])
        for network in inventory:
            for station in network:
                writer.writerow([station.code, station.latitude, station.longitude, station.elevation])
    print(f"Coordinate salvate in {STATION_CSV_PATH}")


def main() -> None:
    fetch_stationxml()
    stationxml_to_csv()


if __name__ == "__main__":
    main()