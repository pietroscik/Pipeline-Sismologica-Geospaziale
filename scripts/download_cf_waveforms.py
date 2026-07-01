#!/usr/bin/env python3
"""Scarica waveform MiniSEED per un elenco di stazioni (configurabile via CLI o config.yaml)."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import concurrent.futures
from typing import Iterable, Sequence, Tuple
from datetime import datetime

from obspy import UTCDateTime
from obspy.clients.fdsn import Client

from utils import setup_logger, load_config

logger = setup_logger("download_mseed")

DEFAULT_START = "2005-01-01T00:00:00"
DEFAULT_END = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
DEFAULT_BLOCK_DAYS = 30


def read_list_argument(values: Sequence[str] | None, file_path: Path | None) -> list[str]:
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
        "--config", type=str,
        help="File YAML con configurazione (sovrascrive i parametri CLI)."
    )
    parser.add_argument("--network", "--networks", default=fdsn_cfg.get("network", "IV"), help="Codice network FDSN.")
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
        help="Durata blocchi di download in giorni (default: 30).",
    )
    parser.add_argument(
        "--client",
        default=fdsn_cfg.get("client", "INGV"),
        help="Nodo FDSN da usare.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / fdsn_cfg.get("output_dir", "data/raw/waveforms"),
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


def iter_time_windows(start: UTCDateTime, end: UTCDateTime, block_days: float) -> Iterable[tuple[UTCDateTime, UTCDateTime]]:
    """Genera intervalli [start, min(start+block, end)]."""
    block_seconds = max(block_days, 0.1) * 24 * 3600
    current = start
    while current < end:
        window_end = min(current + block_seconds, end)
        yield current, window_end
        current = window_end


def _download_worker(
    client_name: str, network: str, location: str, station: str,
    channel: str, window_start: UTCDateTime, window_end: UTCDateTime,
    fpath: Path, dry_run: bool
) -> dict:
    """Esegue il singolo download e restituisce un dizionario coi risultati."""
    info = {
        "station": station,
        "channel": channel,
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "path": str(fpath),
        "status": "dry_run" if dry_run else "failed"
    }
    if dry_run:
        logger.info(f"[DRY] {station}.{channel} {window_start.date} -> {fpath}")
        return info

    try:
        client = Client(client_name, timeout=45)
        stream = client.get_waveforms(
            network=network, station=station, location=location,
            channel=channel, starttime=window_start, endtime=window_end,
            attach_response=False,
        )
        stream.write(str(fpath), format="MSEED")
        logger.info(f"[OK] {fpath}")
        info["status"] = "success"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"{station}.{channel} {window_start.date} -> {exc}")
    return info


def download_waveforms() -> None:
    config = load_config()
    fdsn_cfg = config.get("fdsn", {})
    args = parse_arguments(fdsn_cfg)

    default_stations = fdsn_cfg.get("stations", [])
    default_channels = fdsn_cfg.get("channels", ["HHZ", "HHN", "HHE", "EHZ", "EHN", "EHE"])

    stations = read_list_argument(args.stations, args.stations_file) or default_stations
    channels = read_list_argument(args.channels, args.channels_file) or default_channels

    start = UTCDateTime(args.start)
    end = UTCDateTime(args.end)
    if end <= start:
        raise SystemExit("L'intervallo temporale è invalido: --end deve essere maggiore di --start.")

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Raccogliamo tutti i task necessari in una lista piatta
    tasks = []
    for station in stations:
        station_dir = output_dir / station
        station_dir.mkdir(parents=True, exist_ok=True)
        for channel in channels:
            for window_start, window_end in iter_time_windows(start, end, args.block_days):
                fname = f"{window_start.strftime('%Y%m%dT%H%M%S')}_{args.network}_{station}_{channel}.mseed"
                fpath = station_dir / fname
                tasks.append((station, channel, window_start, window_end, fpath))

    # 2. Eseguiamo il pool di thread limitato dal parametro --workers
    summary = []
    logger.info(f"Avvio download parallelo con {args.workers} worker per {len(tasks)} task totali...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _download_worker,
                args.client, args.network, args.location,
                t[0], t[1], t[2], t[3], t[4], args.dry_run
            ): t for t in tasks
        }
        
        # as_completed restituisce i risultati mano a mano che i thread terminano
        for future in concurrent.futures.as_completed(futures):
            try:
                summary.append(future.result())
            except Exception as e:
                logger.error(f"Eccezione inattesa nel worker: {e}")

    if args.dry_run:
        # Salva un CSV riassuntivo nella cartella di output per comodità.
        summary_csv = args.output_dir / "dry_run_summary.csv"
        summary_csv.parent.mkdir(parents=True, exist_ok=True)
        with summary_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["station", "channel", "start", "end", "path"])
            writer.writeheader()
            writer.writerows(summary)
        logger.info(f"Simulazione completata. Report: {summary_csv}")


if __name__ == "__main__":
    download_waveforms()
