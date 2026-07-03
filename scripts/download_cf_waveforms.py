#!/usr/bin/env python3

"""Scarica waveform MiniSEED per un elenco di stazioni (configurabile via CLI o config.yaml)."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence, Tuple

from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from tqdm import tqdm
from scripts.utils import load_config, setup_logger

logger = setup_logger("download_mseed")


DEFAULT_START = "2005-01-01T00:00:00"

DEFAULT_END = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

DEFAULT_BLOCK_DAYS = 1


DEFAULT_FDSN_CLIENTS = [
    "INGV",
    "IRIS",
    "GEOFON",
    "RASPISHAKE",
]

FDSN_CLIENT_ALIAS = {
    "INGV": "http://webservices.ingv.it",
    "IRIS": "http://service.iris.edu",
    "GEOFON": "http://geofon.gfz-potsdam.de",
    "RASPISHAKE": "http://fdsnws.raspberryshakedata.com",
}

INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename_component(value: str) -> str:
    sanitized = value.strip()
    for ch in INVALID_FILENAME_CHARS:
        sanitized = sanitized.replace(ch, "_")
    return sanitized


def normalize_fdsn_client_url(client_url: str) -> str:
    url = client_url.strip()
    upper = url.upper()
    if upper in FDSN_CLIENT_ALIAS:
        return FDSN_CLIENT_ALIAS[upper]
    if "/FDSNWS/" in upper:
        idx = upper.index("/FDSNWS/")
        url = url[:idx]
    return url.rstrip("/")


def resolve_fdsn_client_urls(args, fdsn_cfg: dict) -> list[str]:
    if getattr(args, "clients", None):
        return [
            normalize_fdsn_client_url(url)
            for url in args.clients
            if url and url.strip()
        ]
    if getattr(args, "client", None):
        return [normalize_fdsn_client_url(args.client)]

    if fdsn_cfg.get("clients"):
        return [
            normalize_fdsn_client_url(url)
            for url in fdsn_cfg["clients"]
            if url and str(url).strip()
        ]
    if fdsn_cfg.get("client"):
        return [normalize_fdsn_client_url(fdsn_cfg["client"])]

    return [normalize_fdsn_client_url(url) for url in DEFAULT_FDSN_CLIENTS]


def read_list_argument(
    values: Sequence[str] | None, file_path: Path | None
) -> list[str]:
    """Raccoglie elementi da CLI e da file (CSV o testo semplice)."""

    items: list[str] = []

    if file_path:

        raw_text = file_path.read_text(encoding="utf-8").splitlines()

        for line in raw_text:

            line = line.strip()

            if not line or line.startswith("#"):

                continue

            if "," in line:

                parts = [token.strip() for token in line.split(",")]

            else:

                parts = line.split()

            items.extend(filter(None, parts))

    if values:

        items.extend([token for token in values if token])

    # Rimuove duplicati preservando l'ordine

    seen: set[str] = set()

    deduped: list[str] = []

    for item in items:

        key = item.strip()

        if not key or key in seen:

            continue

        seen.add(key)

        deduped.append(key)

    return deduped


def parse_arguments(fdsn_cfg: dict) -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="Scarica waveform MiniSEED via FDSN per un elenco di stazioni.",
    )

    parser.add_argument(
        "--config",
        type=str,
        help="File YAML con configurazione (sovrascrive i parametri CLI).",
    )

    parser.add_argument(
        "--network",
        "--networks",
        default=fdsn_cfg.get("network", "IV"),
        help="Codice network FDSN.",
    )

    parser.add_argument(
        "--stations",
        nargs="*",
        help="Lista di codici stazione (puoi ripetere l'opzione). Se omesso, usa elenco predefinito.",
    )

    parser.add_argument(
        "--stations-file",
        type=Path,
        help="File testo/CSV con codici stazione (uno per riga o separati da virgole).",
    )

    parser.add_argument(
        "--channels",
        nargs="*",
        help="Lista di canali da scaricare (es. HHZ HHE).",
    )

    parser.add_argument(
        "--channels-file",
        type=Path,
        help="File con codici canale (uno per riga o CSV).",
    )

    parser.add_argument(
        "--start",
        default=DEFAULT_START,
        help=f"Data/ora iniziale (formato ISO, default: {DEFAULT_START}).",
    )

    parser.add_argument(
        "--end",
        default=DEFAULT_END,
        help=f"Data/ora finale (formato ISO, default: {DEFAULT_END}).",
    )

    parser.add_argument(
        "--block-days",
        type=float,
        default=DEFAULT_BLOCK_DAYS,
        help="Durata blocchi di download in giorni (default: 1).",
    )

    parser.add_argument(
        "--client",
        default=None,
        help="Nodo FDSN singolo da usare (base URL o nome).",
    )

    parser.add_argument(
        "--clients",
        nargs="+",
        default=None,
        help="Lista di nodi FDSN da provare in ordine.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / fdsn_cfg.get("output_dir", "data/raw/waveforms"),
        help="Cartella di destinazione per i MiniSEED.",
    )

    parser.add_argument(
        "--location",
        default="*",
        help="Codice location FDSN (default: *).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra cosa verrebbe scaricato senza contattare il server.",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Numero di thread paralleli per il download (default: 4).",
    )

    return parser.parse_args()


def iter_time_windows(
    start: UTCDateTime, end: UTCDateTime, block_days: float
) -> Iterable[tuple[UTCDateTime, UTCDateTime]]:
    """Genera intervalli [start, min(start+block, end)]."""

    block_seconds = max(block_days, 0.1) * 24 * 3600

    current = start

    while current < end:

        window_end = min(current + block_seconds, end)

        yield current, window_end

        current = window_end


def _download_worker(
    client_urls: list[str],
    network: str,
    location: str,
    station: str,
    channel: str,
    window_start: UTCDateTime,
    window_end: UTCDateTime,
    fpath: Path,
    dry_run: bool,
) -> dict:
    info = {
        "network": network,
        "station": station,
        "location": location,
        "channel": channel,
        "start": str(window_start),
        "end": str(window_end),
        "path": str(fpath),
        "status": "failed",
        "client": None,
        "error": None,
    }

    if dry_run:
        info["status"] = "dry-run"
        return info

    for candidate in client_urls:
        try:
            logger.info(f"Provo client FDSN: {candidate}")
            client = Client(candidate, timeout=60)
            st = client.get_waveforms(
                network=network,
                station=station,
                location=location,
                channel=channel,
                starttime=window_start,
                endtime=window_end,
            )
            st.write(str(fpath), format="MSEED")
            info["status"] = "success"
            info["client"] = candidate
            return info
        except Exception as exc:
            msg = str(exc)
            logger.warning(f"Client {candidate} fallito: {msg}")
            # Detect common "too much data / 413" responses and signal oversize
            if (
                "Request Entity Too Large" in msg
                or "maximum number" in msg
                or "413" in msg
            ):
                info["status"] = "oversize"
                info["error"] = msg
                return info
            # otherwise continue to next candidate

    info["error"] = "Nessun client FDSN risponde"
    return info


def download_waveforms() -> None:

    config = load_config()

    fdsn_cfg = config.get("fdsn", {})

    args = parse_arguments(fdsn_cfg)

    client_urls = resolve_fdsn_client_urls(args, fdsn_cfg)

    default_stations = fdsn_cfg.get("stations", [])

    default_channels = fdsn_cfg.get(
        "channels", ["HHZ", "HHN", "HHE", "EHZ", "EHN", "EHE"]
    )

    stations = read_list_argument(args.stations, args.stations_file) or default_stations

    channels = read_list_argument(args.channels, args.channels_file) or default_channels

    start = UTCDateTime(args.start)

    end = UTCDateTime(args.end)

    if end <= start:

        raise SystemExit(
            "L'intervallo temporale è invalido: --end deve essere maggiore di --start."
        )

    output_dir = args.output_dir.expanduser()

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Raccogliamo tutti i task necessari in una lista piatta

    tasks = []

    for station in stations:
        safe_station = sanitize_filename_component(station)
        station_dir = output_dir / safe_station
        station_dir.mkdir(parents=True, exist_ok=True)

        for channel in channels:
            safe_channel = sanitize_filename_component(channel)
            safe_network = sanitize_filename_component(args.network)

            for window_start, window_end in iter_time_windows(
                start, end, args.block_days
            ):
                fname = f"{window_start.strftime('%Y%m%dT%H%M%S')}_{safe_network}_{safe_station}_{safe_channel}.mseed"
                fpath = station_dir / fname
                tasks.append((station, channel, window_start, window_end, fpath))

    # 2. Eseguiamo il pool di thread limitato dal parametro --workers

    summary = []

    logger.info(
        f"Avvio download parallelo con {args.workers} worker per {len(tasks)} task totali..."
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _download_worker,
                client_urls,
                args.network,
                args.location,
                t[0],  # station
                t[1],  # channel
                t[2],  # window_start (UTCDateTime)
                t[3],  # window_end (UTCDateTime)
                t[4],  # fpath
                args.dry_run,
            ): t
            for t in tasks
        }

        # as_completed restituisce i risultati mano a mano che i thread terminano
        oversize_tasks: list[tuple] = []
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(tasks), desc="Download Waveforms"):
            t = futures[future]
            try:
                res = future.result()
                summary.append(res)
                if res.get("status") == "oversize":
                    oversize_tasks.append(t)
            except Exception as e:
                logger.error(f"Eccezione inattesa nel worker: {e}")

    if args.dry_run:

        # Salva un CSV riassuntivo nella cartella di output per comodità.

        summary_csv = args.output_dir / "dry_run_summary.csv"

        summary_csv.parent.mkdir(parents=True, exist_ok=True)

        with summary_csv.open("w", newline="", encoding="utf-8") as fh:

            writer = csv.DictWriter(
                fh, fieldnames=["station", "channel", "start", "end", "path"]
            )

            writer.writeheader()

            writer.writerows(summary)

        logger.info(f"Simulazione completata. Report: {summary_csv}")


if __name__ == "__main__":

    download_waveforms()
