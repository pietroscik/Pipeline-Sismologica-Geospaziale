import argparse
import pandas as pd
from pathlib import Path
import numpy as np


def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggrega i dati degli eventi su base oraria e calcola le feature.
    """
    if df.empty:
        return pd.DataFrame()

    # Converte il timestamp in formato datetime e lo imposta come indice
    df['Tempo'] = pd.to_datetime(df['arrival_epoch'], unit='s')
    df = df.set_index('Tempo')

    # Definisce l'energia (qui ipotizzata come delta_seconds, da adattare se hai una metrica migliore)
    # Per un calcolo realistico dell'energia, dovresti usare l'ampiezza del segnale.
    df['energia'] = df['delta_seconds'].abs()

    # Aggregazione oraria
    # 'h' è il codice per l'aggregazione oraria
    agg_rules = {
        'numero_eventi': ('event_id', 'count'),
        'energia_max': ('energia', 'max'),
        'energia_media': ('energia', 'mean'),
        'energia_std': ('energia', 'std'),
        'energia_min': ('energia', 'min')
    }
    hourly_features = df.resample('h').agg(**agg_rules).fillna(0)

    # Calcolo delle feature su finestre temporali (rolling windows)
    # Le finestre includono il punto corrente (closed='right')
    for window in [6, 12, 24, 48]:
        # Calcolo per 'numero_eventi'
        rolling_events = hourly_features['numero_eventi'].rolling(
            window=window, min_periods=1, closed='right'
        )
        hourly_features[f'eventi_ultime_{window}h'] = rolling_events.sum()

        # Calcolo per 'energia'
        # Per le statistiche sull'energia, dobbiamo tornare al dataframe originale (non aggregato)
        # e applicare una finestra mobile su di esso, poi ri-campionare.
        rolling_energy_max = df['energia'].rolling(f'{window}h', closed='right').max()
        rolling_energy_mean = df['energia'].rolling(f'{window}h', closed='right').mean()
        rolling_energy_std = df['energia'].rolling(f'{window}h', closed='right').std()

        # Ri-campiona i risultati per allinearli all'output orario
        hourly_features[f'energia_max_ultime_{window}h'] = rolling_energy_max.resample('h').last().fillna(0)
        hourly_features[f'energia_media_ultime_{window}h'] = rolling_energy_mean.resample('h').last().fillna(0)
        hourly_features[f'energia_std_ultime_{window}h'] = rolling_energy_std.resample('h').last().fillna(0)

    # Calcolo dei trend (semplice differenza)
    for trend_window in [12, 24]:
        # Trend sul numero di eventi
        # Confronta la somma degli eventi nella prima metà della finestra con la seconda metà
        first_half_events = hourly_features['numero_eventi'].rolling(window=trend_window // 2, closed='right').sum()
        total_events = hourly_features['numero_eventi'].rolling(window=trend_window, closed='right').sum()
        second_half_events = total_events - first_half_events
        hourly_features[f'trend_eventi_{trend_window}h'] = second_half_events - first_half_events

    # Aggiunta di feature temporali
    hourly_features['ora_del_giorno'] = hourly_features.index.hour
    hourly_features['giorno_della_settimana'] = hourly_features.index.dayofweek
    hourly_features['is_notte'] = ((hourly_features.index.hour >= 22) | (hourly_features.index.hour <= 6)).astype(int)
    hourly_features['is_weekend'] = (hourly_features.index.dayofweek >= 5).astype(int)

    # NOTA: Le colonne come 'bvalue', 'skewness', 'kurtosis', 'entropy' richiedono
    # implementazioni più complesse e specifiche che vanno oltre questo esempio base.

    return hourly_features.reset_index()


def main():
    parser = argparse.ArgumentParser(
        description="Crea un dataset per ML aggregando i dati degli eventi sismici."
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Percorso del file CSV con gli eventi grezzi (output di compute_mseed_deltas.py)."
    )
    parser.add_argument(
        "output_csv",
        type=Path,
        help="Percorso dove salvare il dataset per ML finale."
    )
    args = parser.parse_args()

    print(f"Caricamento dati da: {args.input_csv}")
    try:
        raw_events_df = pd.read_csv(args.input_csv)
    except FileNotFoundError:
        print(f"Errore: File non trovato in {args.input_csv}")
        return

    print("Calcolo delle feature in corso...")
    ml_dataset = calculate_features(raw_events_df)

    print(f"Salvataggio del dataset per ML in: {args.output_csv}")
    ml_dataset.to_csv(args.output_csv, index=False, float_format='%.5f')
    print("Completato.")


if __name__ == "__main__":
    main()