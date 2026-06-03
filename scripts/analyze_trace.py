#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from obspy import read
from obspy.core.trace import Trace
from obspy.signal.trigger import classic_sta_lta
from scipy.signal import welch

from utils import setup_logger

logger = setup_logger("analyze_trace")


def plot_waveform(trace: Trace, outdir: Path | None = None) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    times = trace.times("matplotlib")
    # Decimazione visuale per array troppo grandi (evita ArrayMemoryError)
    skip = max(1, trace.stats.npts // 500_000)
    ax.plot(times[::skip], trace.data[::skip], "k-", linewidth=0.8)
    ax.xaxis_date()
    ax.set_title(f"{trace.id} | {trace.stats.starttime} to {trace.stats.endtime}")
    ax.set_xlabel("Tempo")
    ax.set_ylabel("Ampiezza (counts)")
    fig.autofmt_xdate()
    fig.tight_layout()
    if outdir:
        outfile = outdir / f"{trace.id.replace('.', '_')}_waveform.png"
        fig.savefig(outfile, dpi=150)
        logger.info(f"Waveform salvato in {outfile}")
    else:
        plt.show()
    plt.close(fig)


def plot_fft(trace: Trace, outdir: Path | None = None) -> None:
    sr = trace.stats.sampling_rate
    npts = trace.stats.npts
    # Usa Welch per calcolare lo spettro su grossi array (evita MemoryError)
    nperseg = min(npts, int(sr * 60))  # finestre di 60 secondi
    fft_freq, pxx = welch(trace.data.astype(float), fs=sr, nperseg=nperseg)
    amplitude = np.sqrt(pxx)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.semilogy(fft_freq, amplitude, "b-")
    ax.set_title("Spettro di ampiezza")
    ax.set_xlabel("Frequenza (Hz)")
    ax.set_ylabel("Ampiezza")
    ax.grid(True, which="both", linewidth=0.3)
    fig.tight_layout()
    if outdir:
        outfile = outdir / f"{trace.id.replace('.', '_')}_fft.png"
        fig.savefig(outfile, dpi=150)
        logger.info(f"FFT salvato in {outfile}")
    else:
        plt.show()
    plt.close(fig)


def plot_sta_lta(trace: Trace, sta: float, lta: float, outdir: Path | None = None) -> None:
    sr = trace.stats.sampling_rate
    nsta = max(1, int(sr * sta))
    nlta = max(nsta + 1, int(sr * lta))
    cft = classic_sta_lta(trace.data, nsta, nlta)
    times = trace.times()
    skip = max(1, trace.stats.npts // 500_000)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times[::skip], cft[::skip], "r-")
    ax.set_title(f"CFT STA/LTA (STA={sta}s, LTA={lta}s)")
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel("Ratio")
    ax.grid(True, linewidth=0.3)
    fig.tight_layout()
    if outdir:
        outfile = outdir / f"{trace.id.replace('.', '_')}_sta_lta.png"
        fig.savefig(outfile, dpi=150)
        logger.info(f"STA/LTA salvato in {outfile}")
    else:
        plt.show()
    plt.close(fig)


def summarize_trace(trace: Trace, sta: float, lta: float) -> dict[str, float]:
    data = trace.data.astype(float)
    sr = trace.stats.sampling_rate
    duration = trace.stats.delta * trace.stats.npts
    peak_idx = int(np.argmax(np.abs(data)))
    peak_time = trace.stats.starttime.timestamp + peak_idx / sr
    nsta = max(1, int(sr * sta))
    nlta = max(nsta + 1, int(sr * lta))
    cft = classic_sta_lta(data, nsta, nlta)
    cft_peak_idx = int(np.argmax(cft))
    cft_peak_time = trace.stats.starttime.timestamp + cft_peak_idx / sr

    nperseg = min(trace.stats.npts, int(sr * 60))
    freqs, pxx = welch(data, fs=sr, nperseg=nperseg)
    peak_freq_idx = int(np.argmax(pxx))
    peak_freq = freqs[peak_freq_idx] if freqs.size else 0.0

    summary = {
        "trace_id": trace.id,
        "start_time_iso": str(trace.stats.starttime),
        "end_time_iso": str(trace.stats.endtime),
        "duration_s": float(duration),
        "sampling_rate_hz": float(sr),
        "npts": int(trace.stats.npts),
        "amplitude_peak": float(data[peak_idx]),
        "amplitude_peak_time_epoch": float(peak_time),
        "amplitude_rms": float(np.sqrt(np.mean(data ** 2))),
        "amplitude_std": float(np.std(data)),
        "percentile_95": float(np.percentile(np.abs(data), 95)),
        "sta_lta_peak": float(cft[cft_peak_idx]) if cft.size else float("nan"),
        "sta_lta_peak_time_epoch": float(cft_peak_time),
        "sta_lta_mean": float(np.mean(cft)) if cft.size else float("nan"),
        "frequency_peak_hz": float(peak_freq),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analizza una traccia MiniSEED (waveform, spettro, STA/LTA).")
    parser.add_argument("--file", type=Path, help="Percorso al file MiniSEED.")
    parser.add_argument("--dir", type=Path, help="Cartella contenente file MiniSEED da analizzare in batch.")
    parser.add_argument("--output-csv", type=Path, help="File CSV di output per le statistiche batch.")
    parser.add_argument("--component", help="Se il file contiene più tracce, specifica la component (es. 'HHZ').")
    parser.add_argument("--sta", type=float, default=1.0, help="Finestra STA in secondi.")
    parser.add_argument("--lta", type=float, default=10.0, help="Finestra LTA in secondi.")
    parser.add_argument("--freqmin", type=float, help="Filtro passa-basso minimo (Hz).")
    parser.add_argument("--freqmax", type=float, help="Filtro passa-basso massimo (Hz).")
    parser.add_argument("--outdir", type=Path, help="Se impostata, salva i grafici nella cartella.")
    parser.add_argument("--no-plots", action="store_true", help="Disabilita la generazione dei grafici (utile in batch).")
    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("Devi specificare --file oppure --dir")

    files_to_process = list(args.dir.rglob("*.mseed")) if args.dir else [args.file]

    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)

    all_summaries = []

    for fpath in files_to_process:
        try:
            st = read(str(fpath))
        except Exception as exc:
            logger.warning(f"Impossibile leggere {fpath}: {exc}")
            continue

        if args.component:
            st = st.select(component=args.component)
            if len(st) == 0:
                logger.warning(f"Nessuna traccia con component {args.component} in {fpath}")
                continue

        trace = st[0]
        trace = trace.copy()

        if args.freqmin or args.freqmax:
            fmin = args.freqmin or 0.01
            fmax = args.freqmax or (0.4 * trace.stats.sampling_rate)
            trace.detrend("demean")
            trace.taper(max_percentage=0.05, type="cosine")
            trace.filter("bandpass", freqmin=fmin, freqmax=fmax, corners=4, zerophase=True)

        if not args.no_plots:
            plot_waveform(trace, args.outdir)
            plot_fft(trace, args.outdir)
            plot_sta_lta(trace, args.sta, args.lta, args.outdir)

        summary = summarize_trace(trace, args.sta, args.lta)
        summary["filename"] = fpath.name
        all_summaries.append(summary)

        if args.dir:
            logger.info(f"Elaborato: {fpath.name}")
        else:
            logger.info("\n" + json.dumps(summary, indent=2))

    if args.dir and args.output_csv and all_summaries:
        df = pd.DataFrame(all_summaries)
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.output_csv, index=False)
        logger.info(f"Statistiche batch salvate in {args.output_csv} ({len(df)} file).")


if __name__ == "__main__":
    main()
