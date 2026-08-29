"""Pytest configuration and common fixtures."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolate_alert_storage(tmp_path, monkeypatch):
    """Prevent tests from appending alerts to the repository's runtime logs."""
    import mobile.alert_system as alert_system

    monkeypatch.setattr(alert_system, "PROJECT_ROOT", tmp_path)


@pytest.fixture
def sample_stations_csv():
    return pd.DataFrame(
        {
            "station": ["ST01", "ST02", "ST03"],
            "latitude": [40.8, 40.9, 40.7],
            "longitude": [14.1, 14.2, 14.0],
            "elevation": [100, 150, 120],
        }
    )


@pytest.fixture
def sample_delta_csv():
    return pd.DataFrame(
        {
            "station": ["ST01", "ST02", "ST03", "ST01", "ST02"],
            "delta_seconds": [0.5, -0.2, 0.8, 0.3, -0.1],
            "channel": ["HHZ", "HHZ", "HHZ", "HHN", "HHE"],
        }
    )


@pytest.fixture
def sample_events_csv():
    return pd.DataFrame(
        {
            "event_id": [1, 2, 3],
            "time": [
                "2024-01-01T00:00:00",
                "2024-01-01T01:00:00",
                "2024-01-01T02:00:00",
            ],
            "latitude": [40.8, 40.9, 40.7],
            "longitude": [14.1, 14.2, 14.0],
        }
    )


@pytest.fixture
def temp_csv_file(tmp_path, request):
    df = request.param
    file_path = tmp_path / "test.csv"
    df.to_csv(file_path, index=False)
    return file_path


@pytest.fixture
def mock_environment(monkeypatch):
    def _mock_env(values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    return _mock_env
