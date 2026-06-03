"""Data validation utilities for mobile analysis."""

import pandas as pd
import numpy as np
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def validate_data(df: pd.DataFrame, required_columns: list = None) -> Tuple[bool, str, Optional[pd.DataFrame]]:
    if required_columns is None:
        required_columns = ['event_id', 'station', 'delta_seconds', 'arrival_iso']
    
    errors = []
    warnings = []
    
    if df.empty:
        return False, "DataFrame is empty", None
    
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        return False, f"Missing required columns: {missing_cols}", None
    
    missing_values = df[required_columns].isnull().sum()
    cols_with_missing = missing_values[missing_values > 0].index.tolist()
    if cols_with_missing:
        errors.append(f"Missing values in columns: {cols_with_missing}")
    
    if 'delta_seconds' in df.columns:
        q1 = df['delta_seconds'].quantile(0.01)
        q3 = df['delta_seconds'].quantile(0.99)
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        outliers = df[(df['delta_seconds'] < lower_bound) | (df['delta_seconds'] > upper_bound)]
        if len(outliers) > 0:
            warnings.append(f"Found {len(outliers)} outliers in delta_seconds (IQR method)")
            df.loc[df['delta_seconds'] < lower_bound, 'delta_seconds'] = lower_bound
            df.loc[df['delta_seconds'] > upper_bound, 'delta_seconds'] = upper_bound
    
    if 'arrival_iso' in df.columns:
        try:
            df['arrival_iso'] = pd.to_datetime(df['arrival_iso'], errors='coerce')
            if df['arrival_iso'].isnull().any():
                errors.append("Invalid date format in arrival_iso")
            elif (df['arrival_iso'] > pd.Timestamp.now()).any():
                errors.append("Found future dates in arrival_iso")
        except Exception as e:
            errors.append(f"Error parsing dates: {str(e)}")
    
    if 'station' in df.columns:
        invalid_stations = df[~df['station'].str.match(r'^[A-Z0-9]{3,5}$', na=False)]
        if len(invalid_stations) > 0:
            warnings.append(f"Found {len(invalid_stations)} stations with invalid format")
    
    for warning in warnings:
        logger.warning(warning)
    for error in errors:
        logger.error(error)
    
    if errors:
        return False, "; ".join(errors), df
    
    return True, "OK", df


def validate_stations(df: pd.DataFrame) -> Tuple[bool, str]:
    required_cols = ['station', 'latitude', 'longitude']
    
    if df.empty:
        return False, "Stations dataframe is empty"
    
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return False, f"Missing required columns in stations: {missing_cols}"
    
    if (df['latitude'] < -90).any() or (df['latitude'] > 90).any():
        return False, "Invalid latitude values"
    
    if (df['longitude'] < -180).any() or (df['longitude'] > 180).any():
        return False, "Invalid longitude values"
    
    return True, "OK"


def haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    from math import radians, sin, cos, sqrt, atan2
    R = 6371.0
    lat1, lon1 = radians(lat1), radians(lon1)
    lat2, lon2 = radians(lat2), radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c


def calculate_mean_distance(df: pd.DataFrame, stations: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    event_stations = df['station'].unique()
    station_data = stations[stations['station'].isin(event_stations)]
    if len(station_data) < 2:
        return 0.0
    centroid_lat = station_data['latitude'].mean()
    centroid_lon = station_data['longitude'].mean()
    distances = [haversine_distance(centroid_lon, centroid_lat, row['longitude'], row['latitude']) for _, row in station_data.iterrows()]
    return np.mean(distances)


def calculate_bearing(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    from math import radians, atan2, sin, cos
    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)
    dlon = lon2 - lon1
    y = sin(dlon) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    bearing = np.degrees(atan2(y, x))
    return (bearing + 360) % 360


def calculate_mean_direction(df: pd.DataFrame, center_lon: float, center_lat: float) -> float:
    if df.empty:
        return 0.0
    directions = [calculate_bearing(center_lon, center_lat, row['longitude'], row['latitude']) for _, row in df.iterrows()]
    if len(directions) == 0:
        return 0.0
    x = np.sum(np.cos(np.radians(directions)))
    y = np.sum(np.sin(np.radians(directions)))
    mean_direction = np.degrees(np.arctan2(y, x))
    return (mean_direction + 360) % 360
