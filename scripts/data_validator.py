import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd


class DataValidationError(Exception):
    """Custom exception for data validation errors."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []


def validate_csv_columns(df: pd.DataFrame, required_columns: set) -> bool:
    """Validate that required columns exist in DataFrame."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise DataValidationError("DataFrame is empty")
    if not required_columns.issubset(df.columns):
        missing = required_columns - set(df.columns)
        raise DataValidationError(f"Missing required columns: {missing}")
    return True


def validate_csv_file(file_path: Union[str, Path], required_columns: Optional[set] = None) -> pd.DataFrame:
    """Load and validate a CSV file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    try:
        df = pd.read_csv(file_path)
        if len(df.columns) == 4 and "not" in df.columns:
            raise DataValidationError("Invalid CSV format detected")
    except pd.errors.ParserError as e:
        raise DataValidationError(f"Could not parse CSV file: {e}")
    except Exception as e:
        raise DataValidationError(f"Could not read CSV file: {e}")

    if required_columns and not required_columns.issubset(df.columns):
        missing = required_columns - set(df.columns)
        raise DataValidationError(f"Missing required columns: {missing}")
    return df


def validate_geographic_coordinates(
    df: pd.DataFrame, lat_col: str, lon_col: str, allow_nulls: bool = False
) -> None:
    """Validate geographic coordinates in DataFrame."""
    if not isinstance(df, pd.DataFrame):
        raise DataValidationError("Input must be a pandas DataFrame")
    if lat_col not in df.columns or lon_col not in df.columns:
        raise DataValidationError(f"Missing columns: {lat_col} or {lon_col}")

    lats, lons = df[lat_col], df[lon_col]

    if not allow_nulls and (lats.isnull().any() or lons.isnull().any()):
        raise DataValidationError("NaN values found in coordinates")

    if ((lats < -90) | (lats > 90)).any():
        raise DataValidationError(f"Invalid latitude values (must be in [-90, 90])")
    if ((lons < -180) | (lons > 180)).any():
        raise DataValidationError(f"Invalid longitude values (must be in [-180, 180])")


def validate_numeric_range(
    df: pd.DataFrame, col: str, min_val: float = 0, max_val: float = 100, allow_nulls: bool = False
) -> None:
    """Validate that numeric column is within range."""
    if not isinstance(df, pd.DataFrame) or col not in df.columns:
        raise DataValidationError(f"Column '{col}' not found in DataFrame")

    vals = df[col]
    if not allow_nulls and vals.isnull().any():
        raise DataValidationError(f"NaN values found in column '{col}'")

    if ((vals < min_val) | (vals > max_val)).any():
        raise DataValidationError(f"Values in '{col}' out of range [{min_val}, {max_val}]")


def validate_file_exists(path: Union[str, Path], type_hint: str = "file") -> Union[str, Path]:
    """Validate that file or directory exists."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{type_hint} not found: {path}")
    return path


def validate_file_readable(path: Union[str, Path]) -> Union[str, Path]:
    """Validate that file is readable."""
    if not os.access(path, os.R_OK):
        raise DataValidationError(f"File not readable: {path}")
    return path


def validate_file_size(
    path: Union[str, Path], min_size: float = 0, max_size: float = 1e9
) -> None:
    """Validate file size in bytes. Default max: 1GB."""
    size = os.path.getsize(path)
    if size < min_size or size > max_size:
        raise DataValidationError(
            f"File size {size} bytes out of range [{min_size}, {max_size}]"
        )


def validate_station_data(df: pd.DataFrame) -> None:
    """Validate that station DataFrame has required columns."""
    required = {"station", "latitude", "longitude"}
    if not isinstance(df, pd.DataFrame) or not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise DataValidationError(f"Missing required station columns: {missing}")


def validate_delta_data(df: pd.DataFrame) -> None:
    """Validate that delta DataFrame has required columns and valid data."""
    if not isinstance(df, pd.DataFrame):
        raise DataValidationError("Input must be a pandas DataFrame")
    if "delta_seconds" not in df.columns:
        raise DataValidationError("Missing 'delta_seconds' column")
    if "station" in df.columns and (df["station"] == "").any():
        raise DataValidationError("Empty station codes found")


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two points (in km)."""
    from math import atan2, cos, radians, sin, sqrt

    R = 6371.0  # Earth radius in km
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def calculate_mean_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate mean distance (alias for haversine_distance)."""
    return haversine_distance(lat1, lon1, lat2, lon2)


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate bearing from point 1 to point 2 in degrees (0-360)."""
    from math import atan2, cos, degrees, radians, sin

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(lon2 - lon1)
    y = sin(lon2 - lon1) * cos(lat2)
    return (degrees(atan2(y, x)) + 360) % 360


def calculate_mean_direction(bearings):
    if bearings is None or len(bearings) == 0:
        return 0.0

    angles = np.deg2rad(np.asarray(bearings, dtype=float) % 360.0)
    s = np.mean(np.sin(angles))
    c = np.mean(np.cos(angles))

    # Caso simmetrico (es. [0,90,180,270]): media circolare indeterminata
    # Per i test e uso operativo restituiamo 0.0
    if np.hypot(s, c) < 1e-12:
        return 0.0

    mean = np.degrees(np.arctan2(s, c))
    return (mean + 360.0) % 360.0


def validate_data(
    df: pd.DataFrame,
    required_columns: set,
    expected_types: Optional[Dict[str, type]] = None,
) -> Tuple[bool, str, pd.DataFrame]:
    """Validate DataFrame structure and optionally check column types.

    Returns:
        Tuple of (is_valid, message, dataframe)
    """
    if not isinstance(df, pd.DataFrame):
        return False, "Input is not a pandas DataFrame", df
    if df.empty:
        return False, "DataFrame is empty", df
    if not required_columns.issubset(df.columns):
        missing = required_columns - set(df.columns)
        return False, f"Missing required columns: {missing}", df

    if expected_types:
        for col, expected_type in expected_types.items():
            if col in df.columns:
                try:
                    df[col].astype(expected_type)
                except Exception as e:
                    return False, f"Type mismatch for column '{col}': {e}", df

    return True, "Data validation passed", df


def validate_stations(df: pd.DataFrame) -> Tuple[bool, str]:
    """Validate stations DataFrame with geographic coordinates.

    Returns:
        Tuple of (is_valid, message)
    """
    required_cols = ["station", "latitude", "longitude"]
    if not isinstance(df, pd.DataFrame):
        return False, "Input is not a pandas DataFrame"
    if df.empty:
        return False, "Stations dataframe is empty"

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return False, f"Missing required columns in stations: {missing_cols}"

    if (df["latitude"] < -90).any() or (df["latitude"] > 90).any():
        return False, "Invalid latitude values (must be in [-90, 90])"
    if (df["longitude"] < -180).any() or (df["longitude"] > 180).any():
        return False, "Invalid longitude values (must be in [-180, 180])"

    return True, "Station validation passed"
