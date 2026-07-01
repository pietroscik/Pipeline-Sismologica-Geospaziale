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
    min_stations: int = 3
    coincidence_window: float = 10.0


def parse_filename(path: Path) -> tuple[str, str, str, str]:
    stem = path.stem
    parts = stem.split("_")
    if len(parts) < 4:
        raise ValueError(f"Nome file inatteso: {path.name}")
    event_id, network, station, channel = (
        parts[0],
        parts[1],
        parts[2],
        "_".join(parts[3:]),
    )
    return event_id, network, station, channel


def preprocess(stream: Stream, cfg: PickConfig) -> Stream:

    st = stream.copy()
    st.merge(fill_value="interpolate")

    # 1. Riduzione del volume (Downsampling/Decimazione)
    # Abbassiamo il campionamento a ~50 Hz per risparmiare RAM e CPU
    for tr in st:
        if tr.stats.sampling_rate > 50.0:
            factor = int(tr.stats.sampling_rate // 50.0)
            if factor > 1:
                tr.decimate(factor=factor, no_filter=False)

    st.detrend("demean")
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


def get_all_triggers(stream: Stream, cfg: PickConfig) -> list[float]:
    """Trova tutti i trigger STA/LTA presenti nella traccia continua."""
    tr = stream[0]
    sampling_rate = tr.stats.sampling_rate
    nsta = max(1, int(cfg.sta_seconds * sampling_rate))
    nlta = max(nsta + 1, int(cfg.lta_seconds * sampling_rate))
    if nlta >= len(tr.data):
        return []
    cft = classic_sta_lta(tr.data, nsta, nlta)
    on_off = trigger_onset(cft, cfg.on_threshold, cfg.off_threshold)

    triggers = []
    for onset in on_off:
        onset_sample = onset[0]
        triggers.append(tr.stats.starttime.timestamp + onset_sample / sampling_rate)
    return triggers


def discover_events(
    df_triggers: pd.DataFrame, window_s: float, min_stations: int
) -> pd.DataFrame:
    """Raggruppa i trigger isolati in eventi sismici basandosi sulla coincidenza temporale."""
    logger.info(f"Ricerca coincidenze su {len(df_triggers)} trigger grezzi...")
    df = df_triggers.sort_values("arrival_epoch").copy()
    events = []
    current_cluster = []

    for _, row in df.iterrows():
        if not current_cluster:
            current_cluster.append(row)
            continue

        # Se il trigger rientra nella finestra temporale del primo trigger del cluster
        if row["arrival_epoch"] - current_cluster[0]["arrival_epoch"] <= window_s:
            # Aggiungiamo solo se è una stazione diversa (evita doppi trigger sulla stessa stazione)
            if row["station"] not in [r["station"] for r in current_cluster]:
                current_cluster.append(row)
        else:
            # La finestra è chiusa. Valutiamo se il cluster ha abbastanza stazioni.
            if len(current_cluster) >= min_stations:
                events.append(current_cluster)
            current_cluster = [row]

    if len(current_cluster) >= min_stations:
        events.append(current_cluster)

    logger.info(
        f"Trovati {len(events)} eventi (cluster) con almeno {min_stations} stazioni coincidenti."
    )
    final_rows = []
    for i, cluster in enumerate(events):
        ev_id = f"AUTO_EV_{i+1:04d}"
        for r in cluster:
            r_dict = r.to_dict()
            r_dict["event_id"] = ev_id
            final_rows.append(r_dict)

    return pd.DataFrame(final_rows)


def iter_mseed_files(directory: Path) -> Iterable[Path]:
    yield from sorted(directory.rglob("*.mseed"))


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Calcola delta temporali dalle tracce MiniSEED."
    )
    parser.add_argument("--mseed-dir", required=True, type=Path)
    parser.add_argument(
        "--output-csv",
        default=project_root / "data" / "interim" / "station_deltas.csv",
        type=Path,
    )
    parser.add_argument("--freqmin", type=float, default=1.0)
    parser.add_argument("--freqmax", type=float, default=10.0)
    parser.add_argument("--sta", type=float, default=1.0, help="Finestra STA (s)")
    parser.add_argument("--lta", type=float, default=10.0, help="Finestra LTA (s)")
    parser.add_argument("--thr-on", type=float, default=3.0)
    parser.add_argument("--thr-off", type=float, default=1.5)
    parser.add_argument(
        "--min-stations",
        type=int,
        default=3,
        help="Stazioni minime per confermare l'evento.",
    )
    parser.add_argument(
        "--coincidence-window",
        type=float,
        default=10.0,
        help="Finestra in secondi per raggruppare i trigger.",
    )
    args = parser.parse_args()

    cfg = PickConfig(
        sta_seconds=args.sta,
        lta_seconds=args.lta,
        on_threshold=args.thr_on,
        off_threshold=args.thr_off,
        freqmin=args.freqmin,
        freqmax=args.freqmax,
        min_stations=args.min_stations,
        coincidence_window=args.coincidence_window,
    )

    rows = []
    all_files = list(iter_mseed_files(args.mseed_dir))
    total_files = len(all_files)
    logger.info(f"Trovati {total_files} file MiniSEED da elaborare.")

    for i, path in enumerate(all_files, 1):
        if i % 10 == 0 or i == total_files:
            logger.info(f"Elaborazione file {i}/{total_files}...")
        try:
            event_id, network, station, channel = parse_filename(path)
        except ValueError as exc:
            logger.warning(str(exc))
            continue

        try:
            # Leggiamo il file e uniamo eventuali gap.
            # Questo alloca la memoria base, ma le operazioni matematiche
            # verranno fatte a piccoli blocchi per evitare saturazione RAM.
            st_raw = read(str(path))
            st_raw.merge(fill_value="interpolate")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Impossibile leggere {path.name}: {exc}")
            continue

        tr = st_raw[0]
        sampling_rate_original = tr.stats.sampling_rate
        start_epoch = tr.stats.starttime.timestamp
        end_epoch = tr.stats.endtime.timestamp

        # Chunking: Finestre da 1 ora (3600s) con 60s di overlap
        # Isoliamo i calcoli pesanti su array minuscoli
        window_length = 3600.0
        overlap = max(60.0, cfg.lta_seconds * 3)
        step = window_length - overlap

        triggers = []
        # Scivoliamo lungo il tracciato continuo
        for tr_window in tr.slide(
            window_length=window_length, step=step, include_partial_windows=True
        ):
            st_win = Stream(traces=[tr_window.copy()])
            st_win = preprocess(st_win, cfg)
            win_triggers = get_all_triggers(st_win, cfg)

            is_last = tr_window.stats.endtime >= tr.stats.endtime
            valid_end = tr_window.stats.starttime.timestamp + step

            # Ignoriamo i trigger nella zona di sovrapposizione per non avere duplicati
            for ts in win_triggers:
                if is_last or ts < valid_end:
                    triggers.append(ts)

        for arrival_ts in triggers:
            rows.append(
                dict(
                    filename=str(path),
                    network=network,
                    station=station,
                    channel=channel,
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    sampling_rate=sampling_rate_original,
                    arrival_epoch=arrival_ts,
                )
            )

    if not rows:
        raise SystemExit("Nessun pick trovato. Controlla i parametri o le tracce.")

    df_raw = pd.DataFrame(rows)
    df = discover_events(df_raw, cfg.coincidence_window, cfg.min_stations)

    if df.empty:
        raise SystemExit(
            f"Trovati {len(df_raw)} trigger, ma nessuno forma una coincidenza di rete sufficiente."
        )

    df["arrival_iso"] = pd.to_datetime(df["arrival_epoch"], unit="s")
    df["start_iso"] = pd.to_datetime(df["start_epoch"], unit="s")
    df["end_iso"] = pd.to_datetime(df["end_epoch"], unit="s")
    df["event_reference_epoch"] = df.groupby("event_id")["arrival_epoch"].transform(
        "median"
    )
    df["delta_seconds"] = df["arrival_epoch"] - df["event_reference_epoch"]
    df.sort_values(["event_id", "station"], inplace=True)
    df.to_csv(args.output_csv, index=False)
    logger.info(f"Salvato {args.output_csv} ({len(df)} righe).")


if __name__ == "__main__":
    main()
