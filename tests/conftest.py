"""
Pytest configuration and fixtures for Pipeline Sismologica Geospaziale.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Fixtures for common test data

@pytest.fixture
def sample_stations_csv():
    """Sample stations data as DataFrame."""
    return pd.DataFrame({
        "station": ["ST01", "ST02", "ST03"],
        "latitude": [40.8, 40.9, 40.7],
        "longitude": [14.1, 14.2, 14.0],
        "elevation": [100, 150, 120]
    })


@pytest.fixture
def sample_delta_csv():
    """Sample delta data as DataFrame."""
    return pd.DataFrame({
        "station": ["ST01", "ST02", "ST03", "ST01", "ST02"],
        "delta_seconds": [0.5, -0.2, 0.8, 0.3, -0.1],
        "channel": ["HHZ", "HHZ", "HHZ", "HHN", "HHE"]
    })


@pytest.fixture
def sample_events_csv():
    """Sample events data as DataFrame."""
    return pd.DataFrame({
        "event_id": [1, 2, 3],
        "time": ["2024-01-01T00:00:00", "2024-01-01T01:00:00", "2024-01-01T02:00:00"],
        "latitude": [40.8, 40.9, 40.7],
        "longitude": [14.1, 14.2, 14.0]
    })


@pytest.fixture
def temp_csv_file(tmp_path, request):
    """Create a temporary CSV file from a DataFrame."""
    df = request.param
    file_path = tmp_path / "test.csv"
    df.to_csv(file_path, index=False)
    return file_path


@pytest.fixture
def mock_environment(monkeypatch):
    """Mock environment variables for testing."""
    def _mock_env(vars):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)
    
    return _mock_env
