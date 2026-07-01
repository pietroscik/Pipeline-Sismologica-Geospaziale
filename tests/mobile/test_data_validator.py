"""
Test for data_validator module.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mobile.data_validator import (
    validate_csv_file,
    validate_csv_columns,
    validate_geographic_coordinates,
    validate_numeric_range,
    validate_station_data,
    validate_delta_data,
    validate_file_exists,
    validate_file_readable,
    validate_file_size,
    haversine_distance,
    calculate_mean_distance,
    calculate_bearing,
    calculate_mean_direction,
    DataValidationError,
    validate_data
)


class TestCSVValidation:
    """Tests for CSV file validation functions."""

    def test_validate_csv_columns_valid(self, sample_stations_csv):
        """Test that valid columns pass validation."""
        validate_csv_columns(sample_stations_csv, {"station", "latitude"})

    def test_validate_csv_columns_missing(self, sample_stations_csv):
        """Test that missing columns raise error."""
        with pytest.raises(DataValidationError) as exc_info:
            validate_csv_columns(sample_stations_csv, {"station", "nonexistent"})
        assert "Missing required columns" in str(exc_info.value)

    def test_validate_csv_columns_empty_dataframe(self):
        """Test validation with empty DataFrame."""
        df = pd.DataFrame()
        with pytest.raises(DataValidationError):
            validate_csv_columns(df, {"station"})

    def test_validate_csv_file_valid(self, tmp_path, sample_stations_csv):
        """Test loading valid CSV file."""
        csv_path = tmp_path / "stations.csv"
        sample_stations_csv.to_csv(csv_path, index=False)
        
        df = validate_csv_file(csv_path, required_columns={"station", "latitude"})
        assert len(df) == len(sample_stations_csv)
        assert "station" in df.columns

    def test_validate_csv_file_missing(self, tmp_path):
        """Test loading non-existent CSV file."""
        csv_path = tmp_path / "nonexistent.csv"
        with pytest.raises(FileNotFoundError):
            validate_csv_file(csv_path)

    def test_validate_csv_file_empty(self, tmp_path):
        """Test loading empty CSV file."""
        csv_path = tmp_path / "empty.csv"
        csv_path.touch()
        with pytest.raises(DataValidationError):
            validate_csv_file(csv_path)

    def test_validate_csv_file_invalid_format(self, tmp_path):
        """Test loading invalid CSV file."""
        csv_path = tmp_path / "invalid.csv"
        csv_path.write_text("not,a,valid,csv")
        with pytest.raises(DataValidationError):
            validate_csv_file(csv_path)


class TestGeographicValidation:
    """Tests for geographic coordinate validation."""

    def test_valid_coordinates(self, sample_stations_csv):
        """Test validation of valid coordinates."""
        # Should not raise
        validate_geographic_coordinates(
            sample_stations_csv, "latitude", "longitude"
        )

    def test_invalid_latitude(self):
        """Test detection of invalid latitude."""
        df = pd.DataFrame({
            "station": ["ST01"],
            "latitude": [100.0],  # Invalid: > 90
            "longitude": [14.0]
        })
        with pytest.raises(DataValidationError):
            validate_geographic_coordinates(df, "latitude", "longitude")

    def test_invalid_longitude(self):
        """Test detection of invalid longitude."""
        df = pd.DataFrame({
            "station": ["ST01"],
            "latitude": [40.0],
            "longitude": [200.0]  # Invalid: > 180
        })
        with pytest.raises(DataValidationError):
            validate_geographic_coordinates(df, "latitude", "longitude")

    def test_nan_coordinates(self):
        """Test detection of NaN coordinates."""
        df = pd.DataFrame({
            "station": ["ST01", "ST02"],
            "latitude": [40.0, np.nan],
            "longitude": [14.0, 14.0]
        })
        with pytest.raises(DataValidationError):
            validate_geographic_coordinates(df, "latitude", "longitude")

    def test_allow_nan_coordinates(self):
        """Test allowing NaN coordinates."""
        df = pd.DataFrame({
            "station": ["ST01", "ST02"],
            "latitude": [40.0, np.nan],
            "longitude": [14.0, 14.0]
        })
        # Should not raise with allow_nulls=True
        validate_geographic_coordinates(
            df, "latitude", "longitude", allow_nulls=True
        )

    def test_missing_columns(self):
        """Test detection of missing coordinate columns."""
        df = pd.DataFrame({"station": ["ST01"]})
        with pytest.raises(DataValidationError):
            validate_geographic_coordinates(df, "latitude", "longitude")


class TestNumericValidation:
    """Tests for numeric range validation."""

    def test_valid_range(self):
        """Test validation of values within range."""
        df = pd.DataFrame({"value": [1, 5, 10]})
        validate_numeric_range(df, "value", min_val=0, max_val=15)

    def test_below_min(self):
        """Test detection of values below minimum."""
        df = pd.DataFrame({"value": [-5, 5, 10]})
        with pytest.raises(DataValidationError):
            validate_numeric_range(df, "value", min_val=0, max_val=15)

    def test_above_max(self):
        """Test detection of values above maximum."""
        df = pd.DataFrame({"value": [1, 5, 20]})
        with pytest.raises(DataValidationError):
            validate_numeric_range(df, "value", min_val=0, max_val=15)

    def test_nan_values(self):
        """Test detection of NaN values."""
        df = pd.DataFrame({"value": [1, np.nan, 5]})
        with pytest.raises(DataValidationError):
            validate_numeric_range(df, "value", min_val=0, max_val=10)

    def test_allow_nan_values(self):
        """Test allowing NaN values."""
        df = pd.DataFrame({"value": [1, np.nan, 5]})
        validate_numeric_range(df, "value", min_val=0, max_val=10, allow_nulls=True)


class TestFileValidation:
    """Tests for file validation functions."""

    def test_validate_file_exists_file(self, tmp_path):
        """Test validation of existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        result = validate_file_exists(test_file, "file")
        assert result == test_file

    def test_validate_file_exists_directory(self, tmp_path):
        """Test validation of existing directory."""
        result = validate_file_exists(tmp_path, "directory")
        assert result == tmp_path

    def test_validate_file_exists_missing(self, tmp_path):
        """Test validation of non-existent file."""
        missing_file = tmp_path / "missing.txt"
        with pytest.raises(FileNotFoundError):
            validate_file_exists(missing_file, "file")

    def test_validate_file_readable(self, tmp_path):
        """Test validation of readable file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        result = validate_file_readable(test_file)
        assert result == test_file

    def test_validate_file_size(self, tmp_path):
        """Test file size validation."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("a" * 100)
        
        # Should pass
        validate_file_size(test_file, min_size=50, max_size=200)
        
        # Should fail - too small
        with pytest.raises(DataValidationError):
            validate_file_size(test_file, min_size=200)
        
        # Should fail - too large
        with pytest.raises(DataValidationError):
            validate_file_size(test_file, max_size=50)


class TestStationValidation:
    """Tests for station-specific validation."""

    def test_validate_station_data_valid(self, sample_stations_csv):
        """Test validation of valid station data."""
        validate_station_data(sample_stations_csv)

    def test_validate_station_data_missing_columns(self):
        """Test detection of missing columns in station data."""
        df = pd.DataFrame({"station": ["ST01"]})  # Missing latitude, longitude
        with pytest.raises(DataValidationError):
            validate_station_data(df)

    def test_validate_delta_data_valid(self, sample_delta_csv):
        """Test validation of valid delta data."""
        validate_delta_data(sample_delta_csv)

    def test_validate_delta_data_missing_columns(self):
        """Test detection of missing columns in delta data."""
        df = pd.DataFrame({"station": ["ST01"]})  # Missing delta_seconds
        with pytest.raises(DataValidationError):
            validate_delta_data(df)

    def test_validate_delta_data_empty_stations(self):
        """Test detection of empty station codes."""
        df = pd.DataFrame({
            "station": ["ST01", "", "ST02"],
            "delta_seconds": [0.5, 0.3, 0.2]
        })
        with pytest.raises(DataValidationError):
            validate_delta_data(df)


class TestGeographicFunctions:
    """Tests for geographic calculation functions."""

    def test_haversine_distance(self):
        """Test Haversine distance calculation."""
        # Distance between two points should be symmetric
        dist1 = haversine_distance(40.0, 14.0, 41.0, 15.0)
        dist2 = haversine_distance(41.0, 15.0, 40.0, 14.0)
        assert abs(dist1 - dist2) < 0.001
        
        # Distance to self should be 0
        assert haversine_distance(40.0, 14.0, 40.0, 14.0) == 0

    def test_haversine_distance_known(self):
        """Test Haversine distance with known values."""
        # Approximate distance between Rome and Naples
        dist = haversine_distance(41.9028, 12.4964, 40.8518, 14.2681)
        assert 180 < dist < 200  # ~190 km

    def test_calculate_mean_distance(self):
        """Test mean distance calculation (alias)."""
        dist = calculate_mean_distance(40.0, 14.0, 41.0, 15.0)
        expected = haversine_distance(40.0, 14.0, 41.0, 15.0)
        assert abs(dist - expected) < 0.001

    def test_calculate_bearing(self):
        """Test bearing calculation."""
        # Bearing from (0,0) to (0,1) should be 90 degrees (east)
        bearing = calculate_bearing(0, 0, 0, 1)
        assert abs(bearing - 90) < 0.1
        
        # Bearing from (0,0) to (1,0) should be 0 degrees (north)
        bearing = calculate_bearing(0, 0, 1, 0)
        assert abs(bearing - 0) < 0.1 or abs(bearing - 360) < 0.1

    def test_calculate_mean_direction(self):
        """Test mean direction calculation."""
        # Mean of [0, 90, 180, 270] should be around 0 or 360
        bearings = [0, 90, 180, 270]
        mean_dir = calculate_mean_direction(bearings)
        # Circular mean of these bearings is 0
        assert mean_dir < 10 or mean_dir > 350

    def test_calculate_mean_direction_empty(self):
        """Test mean direction with empty list."""
        mean_dir = calculate_mean_direction([])
        assert mean_dir == 0.0


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing code."""

    def test_validate_data_function(self, sample_stations_csv):
        """Test the validate_data function for backward compatibility."""
        is_valid, message, df_validated = validate_data(
            sample_stations_csv,
            required_columns={"station", "latitude"}
        )
        assert is_valid
        assert "Data validation passed" in message
        assert len(df_validated) == len(sample_stations_csv)

    def test_validate_data_missing_columns(self):
        """Test validate_data with missing columns."""
        df = pd.DataFrame({"station": ["ST01"]})
        is_valid, message, df_validated = validate_data(
            df,
            required_columns={"station", "latitude"}
        )
        assert not is_valid
        assert "Missing required columns" in message

    def test_validate_data_invalid_types(self):
        """Test validate_data with invalid types."""
        df = pd.DataFrame({
            "station": ["ST01", "ST02"],
            "latitude": ["not_a_number", "40.0"]
        })
        is_valid, message, df_validated = validate_data(
            df,
            required_columns={"station", "latitude"},
            expected_types={"latitude": float}
        )
        assert not is_valid
