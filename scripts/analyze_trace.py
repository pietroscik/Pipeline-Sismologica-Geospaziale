#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from obspy import read
from obspy.core.trace import Trace
from obspy.signal.trigger import classic_sta_lta

from utils import setup_logger

logger = setup_logger("analyze_trace")


def plot_waveform(trace: Trace, outdir: Path | None = None) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    times = trace.times("matplotlib")
    ax.plot_date(times, trace.data, "k-", linewidth=0.8)
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
    npts = trace.stats.npts
    dt = trace.stats.delta
    window = np.hanning(npts)
    data = trace.data.astype(float) * window
    fft_vals = np.fft.rfft(data)
    fft_freq = np.fft.rfftfreq(npts, dt)
    amplitude = np.abs(fft_vals)

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

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times, cft, "r-")
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

    freqs = np.fft.rfftfreq(trace.stats.npts, trace.stats.delta)
    spectrum = np.abs(np.fft.rfft(data * np.hanning(trace.stats.npts)))
    peak_freq_idx = int(np.argmax(spectrum))
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
    parser.add_argument("--file", required=True, type=Path, help="Percorso al file MiniSEED.")
    parser.add_argument("--component", help="Se il file contiene più tracce, specifica la component (es. 'HHZ').")
    parser.add_argument("--sta", type=float, default=1.0, help="Finestra STA in secondi.")
    parser.add_argument("--lta", type=float, default=10.0, help="Finestra LTA in secondi.")
    parser.add_argument("--freqmin", type=float, help="Filtro passa-basso minimo (Hz).")
    parser.add_argument("--freqmax", type=float, help="Filtro passa-basso massimo (Hz).")
    parser.add_argument("--outdir", type=Path, help="Se impostata, salva i grafici nella cartella.")
    args = parser.parse_args()

    st = read(str(args.file))
    if args.component:
        st = st.select(component=args.component)
        if len(st) == 0:
            raise SystemExit(f"Nessuna traccia con component {args.component}")

    trace = st[0]
    trace = trace.copy()

    if args.freqmin or args.freqmax:
        fmin = args.freqmin or 0.01
        fmax = args.freqmax or (0.4 * trace.stats.sampling_rate)
        trace.detrend("demean")
        trace.detrend("linear")
        trace.taper(max_percentage=0.05, type="cosine")
        trace.filter("bandpass", freqmin=fmin, freqmax=fmax, corners=4, zerophase=True)

    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)

    plot_waveform(trace, args.outdir)
    plot_fft(trace, args.outdir)
    plot_sta_lta(trace, args.sta, args.lta, args.outdir)

    summary = summarize_trace(trace, args.sta, args.lta)
    logger.info("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
