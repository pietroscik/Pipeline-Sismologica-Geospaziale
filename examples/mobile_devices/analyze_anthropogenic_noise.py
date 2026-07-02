import argparse
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description="Analizza la distribuzione oraria dei trigger per identificare il rumore antropico."
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Path al file CSV georeferenziato (es. output_eventi_georeferenziati.csv.gz)",
    )
    parser.add_argument("--output-file", type=Path, default=None, help="File di testo dove salvare il report.")
    args = parser.parse_args()

    print(f"📖 Caricamento del dataset da {args.input_csv}...")
    df = pd.read_csv(args.input_csv)

    df["arrival_iso"] = pd.to_datetime(df["arrival_iso"], errors="coerce")
    df["ora"] = df["arrival_iso"].dt.hour

    distribuzione_oraria = df["ora"].value_counts().sort_index()

    report_lines = []
    report_lines.append("📊 Distribuzione oraria dei trigger (Analisi Rumore Antropico):")
    report_lines.append("-" * 50)
    for ora, conteggio in distribuzione_oraria.items():
        report_lines.append(f"Ora {ora:02d}:00  -->  {conteggio:,} trigger")
    report_lines.append("-" * 50)

    report_str = "\n".join(report_lines)
    print(f"\n{report_str}")
    if args.output_file:
        args.output_file.write_text(report_str)


if __name__ == "__main__":
    main()