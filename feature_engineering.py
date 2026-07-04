import numpy as np
import pandas as pd


def calculate_rolling_b_value(series: pd.Series, window_size: int) -> pd.Series:
    """
    Calcola il b-value su una finestra mobile (rolling).
    Questa funzione deve essere identica a quella in train_risk_model.py.
    """
    b_values = []
    min_mag_completeness = (
        series[series > 0].min() if not series[series > 0].empty else 0.1
    )

    rolling_windows = series.rolling(window=window_size, min_periods=1)

    for window in rolling_windows:
        if window.mean() > min_mag_completeness:
            denominator = window.mean() - min_mag_completeness
            if denominator > 0:
                b_value = np.log10(np.e) / denominator
                b_values.append(b_value)
            else:
                b_values.append(np.nan)
        else:
            b_values.append(np.nan)
    return pd.Series(b_values, index=series.index).fillna(method="ffill").fillna(0)