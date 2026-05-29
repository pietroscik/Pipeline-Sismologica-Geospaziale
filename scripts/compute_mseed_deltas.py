#!/usr/bin/env python
"""
Esempio d'uso (PowerShell):
    python compute_mseed_deltas.py ^
        --mseed-dir "data/raw/waveforms_campflegrei" ^
        --output-csv "data/interim/station_deltas.csv"
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
from obspy import read
from obspy.core import Stream
from obspy.signal.trigger import classic_sta_lta, trigger_onset

from utils import setup_logger

logger = setup_logger("mseed_deltas")


@dataclass
class PickConfig:
    sta_seconds: float = 1.0
    lta_seconds: float = 10.0
    on_threshold: float = 3.0
    off_threshold: float = 1.5
    freqmin: Optional[float] = 1.0
    freqmax: Optional[float] = 10.0
    taper_percentage: float = 0.05


def parse_filename(path: Path) -> tuple[str, str, str, str]:
    stem = path.stem
    parts = stem.split("_")
    if len(parts) < 4:
        raise ValueError(f"Nome file inatteso: {path.name}")
    event_id, network, station, channel = parts[0], parts[1], parts[2], "_".join(parts[3:])
    return event_id, network, station, channel


def preprocess(stream: Stream, cfg: PickConfig) -> Stream:
    st = stream.copy()
    st.merge(fill_value="interpolate")
    st.detrend("demean")
    st.detrend("linear")
    st.taper(max_percentage=cfg.taper_percentage, type="cosine")
    if cfg.freqmin and cfg.freqmax:
        for tr in st:
            nyquist = 0.5 / tr.stats.delta
            high = min(cfg.freqmax, 0.8 * nyquist)
            low = max(cfg.freqmin, 0.01)
            if high <= low:
                continue
            tr.filter("bandpass", freqmin=low, freqmax=high, corners=4, zerophase=True)
    return st


def pick_arrival(stream: Stream, cfg: PickConfig) -> Optional[float]:
    tr = stream[0]
    sampling_rate = tr.stats.sampling_rate
    nsta = max(1, int(cfg.sta_seconds * sampling_rate))
    nlta = max(nsta + 1, int(cfg.lta_seconds * sampling_rate))
    if nlta >= len(tr.data):
        return None
    cft = classic_sta_lta(tr.data, nsta, nlta)
    on_off = trigger_onset(cft, cfg.on_threshold, cfg.off_threshold)
    if len(on_off) == 0:
        return None
    onset_sample = on_off[0][0]
    return tr.stats.starttime.timestamp + onset_sample / sampling_rate


def iter_mseed_files(directory: Path) -> Iterable[Path]:
    yield from sorted(directory.rglob("*.mseed"))


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Calcola delta temporali dalle tracce MiniSEED.")
    parser.add_argument("--mseed-dir", required=True, type=Path)
    parser.add_argument("--output-csv", default=project_root / "data" / "interim" / "station_deltas.csv", type=Path)
    parser.add_argument("--freqmin", type=float, default=1.0)
    parser.add_argument("--freqmax", type=float, default=10.0)
    parser.add_argument("--sta", type=float, default=1.0, help="Finestra STA (s)")
    parser.add_argument("--lta", type=float, default=10.0, help="Finestra LTA (s)")
    parser.add_argument("--thr-on", type=float, default=3.0)
    parser.add_argument("--thr-off", type=float, default=1.5)
    args = parser.parse_args()

    cfg = PickConfig(
        sta_seconds=args.sta,
        lta_seconds=args.lta,
        on_threshold=args.thr_on,
        off_threshold=args.thr_off,
        freqmin=args.freqmin,
        freqmax=args.freqmax,
    )

    rows = []
    for path in iter_mseed_files(args.mseed_dir):
        try:
            event_id, network, station, channel = parse_filename(path)
        except ValueError as exc:
            logger.warning(str(exc))
            continue

        try:
            st = read(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Impossibile leggere {path.name}: {exc}")
            continue

        st = preprocess(st, cfg)
        arrival_ts = pick_arrival(st, cfg)
        if arrival_ts is None:
            logger.warning(f"Nessun pick per {path.name}")
            continue

        tr = st[0]
        rows.append(
            dict(
                filename=str(path),
                event_id=event_id,
                network=network,
                station=station,
                channel=channel,
                start_epoch=tr.stats.starttime.timestamp,
                end_epoch=tr.stats.endtime.timestamp,
                sampling_rate=tr.stats.sampling_rate,
                arrival_epoch=arrival_ts,
            )
        )

    if not rows:
        raise SystemExit("Nessun pick trovato. Controlla i parametri o le tracce.")

    df = pd.DataFrame(rows)
    df["arrival_iso"] = pd.to_datetime(df["arrival_epoch"], unit="s")
    df["start_iso"] = pd.to_datetime(df["start_epoch"], unit="s")
    df["end_iso"] = pd.to_datetime(df["end_epoch"], unit="s")
    df["event_reference_epoch"] = df.groupby("event_id")["arrival_epoch"].transform("median")
    df["delta_seconds"] = df["arrival_epoch"] - df["event_reference_epoch"]
    df.sort_values(["event_id", "station"], inplace=True)
    df.to_csv(args.output_csv, index=False)
    logger.info(f"Salvato {args.output_csv} ({len(df)} righe).")


if __name__ == "__main__":
    main()
