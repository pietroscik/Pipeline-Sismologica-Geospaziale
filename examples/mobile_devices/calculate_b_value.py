import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def calculate_b_value_from_df(data: pd.DataFrame, magnitude_col: str, min_mag: float, max_mag: float):
    """Calcola il b-value da un DataFrame di eventi."""
    counts = data[magnitude_col].value_counts().sort_index()
    mag_values = counts.index.values
    freq = counts.values

    cum_freq = np.array([np.sum(freq[i:]) for i in range(len(freq))])

    # Evita log(0)
    valid_indices = (cum_freq > 0)
    mag_values = mag_values[valid_indices]
    cum_freq = cum_freq[valid_indices]

    log_cum_freq = np.log10(cum_freq)

    # Filtro per la regressione lineare per garantire la completezza del catalogo
    fit_filter = (mag_values >= min_mag) & (mag_values <= max_mag)
    X = mag_values[fit_filter]
    Y = log_cum_freq[fit_filter]

    if len(X) < 2:
        print("Dati insufficienti per la regressione lineare.")
        return None, None

    slope, intercept = np.polyfit(X, Y, 1)
    b_value = -slope
    return b_value, intercept


def main():
    parser = argparse.ArgumentParser(description="Calcola il b-value (Gutenberg-Richter) da un catalogo di eventi.")
    parser.add_argument("catalog_csv", type=Path, help="Path al file CSV del catalogo eventi.")
    parser.add_argument("--mag-col", default="Numero_Stazioni_Attivate", help="Colonna da usare come proxy della magnitudo.")
    parser.add_argument("--min-mag", type=float, default=6.0, help="Magnitudo minima per il fit.")
    parser.add_argument("--max-mag", type=float, default=15.0, help="Magnitudo massima per il fit.")
    parser.add_argument("--output-file", type=Path, default=None, help="File di testo dove salvare il report.")
    args = parser.parse_args()

    print(f"📖 Caricamento del catalogo da {args.catalog_csv}...")
    df = pd.read_csv(args.catalog_csv)

    b_value, a_value = calculate_b_value_from_df(df, args.mag_col, args.min_mag, args.max_mag)
    
    report_lines = []
    if b_value is not None:
        report_lines.append("📈 Risultati Analisi Gutenberg-Richter:")
        report_lines.append("-" * 40)
        report_lines.append(f"   ↳ b-value stimato: {b_value:.4f}")
        report_lines.append(f"   ↳ Coefficiente 'a' (Attività): {a_value:.4f}")
        
        print("\n".join(report_lines))
        if args.output_file:
            args.output_file.write_text("\n".join(report_lines))

if __name__ == "__main__":
    main()