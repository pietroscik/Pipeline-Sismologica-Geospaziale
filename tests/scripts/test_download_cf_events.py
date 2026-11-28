"""
Test per scripts/download_cf_events.py — downloader del catalogo eventi INGV
(ground truth del Target_Allarme) e per il mkdir dei CSV di output di
compute_mseed_deltas (run reale 2026-11-28: crash dopo 80s di elaborazione
per una directory di output inesistente).
"""
import importlib.util
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

spec = importlib.util.spec_from_file_location(
    "download_cf_events", PROJECT_ROOT / "scripts" / "download_cf_events.py"
)
dce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dce)

SAMPLE_TEXT = "\n".join([
    "#EventID|Time|Latitude|Longitude|Depth/Km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType",
    "100|2026-11-25T22:39:32.130000|40.8255|14.085333|2.5|SURVEY-INGV-OV||||Md|1.1|--|Campi Flegrei|earthquake",
    "101|2026-11-25T23:43:16.890000|40.7100|14.120000|1.8|SURVEY-INGV||||ML|3.9|--|Campi Flegrei|earthquake",
])


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._payload


class TestFetchEvents:
    def test_parsing_formato_text(self):
        df = dce.parse_events_text(SAMPLE_TEXT)
        assert list(df.columns) == list(dce.COLUMN_MAP.values())
        assert len(df) == 2
        assert df["magnitude"].tolist() == [1.1, 3.9]
        assert df["event_location_name"].iloc[0] == "Campi Flegrei"

    def test_url_con_filtro_geografico(self):
        url = dce.build_query_url(
            "https://webservices.ingv.it/fdsnws/event/1/query",
            "2026-11-01T00:00:00", "2026-11-26T00:00:00", 40.82, 14.14, 25,
        )
        assert "latitude=40.82" in url
        assert "longitude=14.14" in url
        assert "maxradius=25" in url
        assert "format=text" in url

    def test_retry_su_errore_transitorio(self):
        """Il primo tentativo fallisce (503), il secondo riesce."""
        with patch.object(dce.time, "sleep") as fake_sleep, \
             patch.object(dce.urllib.request, "urlopen") as fake_open:
            fake_open.side_effect = [
                urllib.error.HTTPError("url", 503, "Service Unavailable", None, None),
                _FakeResponse(SAMPLE_TEXT.encode()),
            ]
            raw = dce.fetch_events_text("https://example.org/query")
        assert raw == SAMPLE_TEXT
        fake_sleep.assert_called()  # backoff applicato
        assert fake_open.call_count == 2

    def test_4xx_permanente_nessun_retry(self):
        """Un 404 (area senza eventi) è permanente: nessun retry."""
        with patch.object(dce.time, "sleep") as fake_sleep, \
             patch.object(dce.urllib.request, "urlopen") as fake_open:
            fake_open.side_effect = urllib.error.HTTPError(
                "url", 404, "Not Found", None, None)
            with pytest.raises(urllib.error.HTTPError):
                dce.fetch_events_text("https://example.org/query")
        fake_sleep.assert_not_called()
        assert fake_open.call_count == 1

    def test_output_csv_crea_directory_padre(self, tmp_path, monkeypatch):
        """Il CSV di output crea le directory padre (lezione della Cella 3)."""
        monkeypatch.setattr(dce, "load_config", lambda *a, **k: {})
        with patch.object(dce, "fetch_events_text", return_value=SAMPLE_TEXT):
            out = tmp_path / "runs" / "real" / "interim" / "catalog_events.csv"
            with patch.object(sys, "argv", [
                "download_cf_events.py", "--start", "2026-11-01T00:00:00",
                "--end", "2026-11-26T00:00:00", "--output-csv", str(out),
            ]):
                dce.main()
        assert out.exists()
        df = pd.read_csv(out)
        assert len(df) == 2

    def test_default_geografici_da_config(self, tmp_path, monkeypatch):
        """Senza --lat/--lon/--radius usa la sezione catalog del config."""
        monkeypatch.setattr(dce, "load_config", lambda *a, **k: {
            "catalog": {"center_lat": 41.0, "center_lon": 15.0, "radius_km": 10},
        })
        url = dce.build_query_url(
            dce.DEFAULT_EVENT_URL, "s", "e",
            dce.load_config()["catalog"]["center_lat"],
            dce.load_config()["catalog"]["center_lon"],
            dce.load_config()["catalog"]["radius_km"],
        )
        assert "latitude=41.0" in url and "maxradius=10" in url


class TestMkdirOutputCsv:
    """compute_mseed_deltas: il CSV di output crea le directory padre.

    Run reale 2026-11-28: la Cella 3 è crashata DOPO 80s di elaborazione
    perché runs/real_ingv/processed/ non esisteva e pandas non la crea.
    """

    def test_mseed_deltas_crea_directory_output(self, tmp_path):
        import subprocess

        mseed_dir = tmp_path / "waveforms"
        mseed_dir.mkdir()
        # Percorso annidato inesistente, come nella run reale
        out = tmp_path / "runs" / "real" / "processed" / "station_deltas.csv"
        result = subprocess.run(
            [
                sys.executable, "-m", "scripts.compute_mseed_deltas",
                "--mseed-dir", str(mseed_dir), "--output-csv", str(out),
            ],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert out.exists()
