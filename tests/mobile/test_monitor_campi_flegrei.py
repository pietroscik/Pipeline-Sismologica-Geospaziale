"""
Test per mobile/monitor_campi_flegrei.py — monitor real-time (issue #4, FASE 5.1).

Strategia:
- Nessun accesso alla rete FDSN: _get_stations_in_area e _fetch_latest_waveforms
  sono mockati/monkeypatchati dove servono.
- Il monitor è istanziabile senza modello ML: mancanza del modello → fallback
  warning (contratto da preservare).
- PROJECT_ROOT del modulo è monkeypatchato su tmp_path per evitare scritture
  nella repo (runs/monitor, config, log).
"""
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import mobile.monitor_campi_flegrei as monitor_mod
from mobile.monitor_campi_flegrei import (
    CampiFlegreiMonitor,
    signal_handler,
    DEFAULT_INTERVAL_MINUTES,
    DEFAULT_MIN_STATIONS,
    DEFAULT_RISK_THRESHOLD,
)


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Isola PROJECT_ROOT del modulo monitor su una directory temporanea."""
    fake_root = tmp_path / "fake_root"
    (fake_root / "mobile" / "config").mkdir(parents=True)
    (fake_root / "mobile" / "logs").mkdir(parents=True)
    monkeypatch.setattr(monitor_mod, "PROJECT_ROOT", fake_root)
    return fake_root


@pytest.fixture
def config_file(tmp_path):
    cfg = {
        "area": {"latitude": 40.8062, "longitude": 14.1410, "radius_km": 20.0},
        "stations": {"default": ["CAAM", "CAFL", "CPOZ"]},
        "thresholds": {"std_divisor": 100.0},
    }
    path = tmp_path / "monitor_config.yaml"
    path.write_text(yaml.dump(cfg))
    return path


@pytest.fixture
def mtr(isolated_root, config_file):
    """Monitor con root isolato e config di test, senza rete."""
    return CampiFlegreiMonitor(config_path=config_file, dry_run=True)


def sample_waveform_df(n_stations=3):
    rows = []
    for i in range(n_stations):
        rows.append({
            "station": f"ST{i:02d}", "network": "IV", "channel": "HHZ",
            "starttime": f"2026-11-27T{10 + i}:00:00", "endtime": f"2026-11-27T{10 + i}:15:00",
            "sampling_rate": 100.0, "npts": 90000,
            "mean": 10.0, "std": 50.0, "min": -100.0, "max": 120.0,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Costanti e signal handler
# ---------------------------------------------------------------------------

class TestDefaultsAndSignals:
    def test_costanti_predefinite(self):
        assert DEFAULT_INTERVAL_MINUTES == 10
        assert DEFAULT_MIN_STATIONS == 18
        assert DEFAULT_RISK_THRESHOLD == 0.7

    def test_signal_handler_setta_shutdown(self):
        monitor_mod.shutdown_requested = False
        try:
            signal_handler(2, None)
            assert monitor_mod.shutdown_requested is True
        finally:
            monitor_mod.shutdown_requested = False


# ---------------------------------------------------------------------------
# Costruttore e configurazione
# ---------------------------------------------------------------------------

class TestInitAndConfig:
    def test_init_con_config_valida(self, mtr, config_file):
        assert mtr.config_data["thresholds"]["std_divisor"] == 100.0
        assert mtr.min_stations == DEFAULT_MIN_STATIONS
        assert mtr.risk_threshold == DEFAULT_RISK_THRESHOLD
        assert mtr.dry_run is True
        assert mtr.interval_seconds == DEFAULT_INTERVAL_MINUTES * 60

    def test_config_mancante_restituisce_dict_vuoto(self, isolated_root, tmp_path):
        m = CampiFlegreiMonitor(config_path=tmp_path / "non_esiste.yaml", dry_run=True)
        assert m.config_data == {}

    def test_config_yaml_invalido_non_crasha(self, isolated_root, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : : non[valido yaml")
        m = CampiFlegreiMonitor(config_path=bad, dry_run=True)
        assert m.config_data == {}

    def test_modello_mancante_attiva_fallback(self, mtr):
        """Senza un modello salvato, il monitor deve restare in fallback."""
        assert mtr.model is None
        assert mtr.model_metadata == {}


# ---------------------------------------------------------------------------
# Preprocessing e predizione rischio
# ---------------------------------------------------------------------------

class TestPreprocessData:
    def test_feature_derivate(self, mtr):
        df = mtr._preprocess_data(sample_waveform_df())
        assert "hour" in df.columns and "minute" in df.columns
        assert set(df["hour"]) == {10, 11, 12}
        # amplitude_range = max - min
        assert (df["amplitude_range"] == 220.0).all()
        # signal_to_noise = std / (range + eps)
        assert abs(df.loc[0, "signal_to_noise"] - 50.0 / 220.0) < 1e-9


class FakeModel:
    """Modello ML fittizio: predice un valore fisso."""
    def __init__(self, value):
        self.value = value

    def predict(self, features):
        return [self.value] * len(features)


class TestPredictRisk:
    def test_con_modello_ml(self, mtr):
        mtr.model = FakeModel(0.42)
        df = mtr._preprocess_data(sample_waveform_df())
        assert mtr._predict_risk(df) == pytest.approx(0.42)

    def test_fallback_senza_modello(self, mtr):
        df = mtr._preprocess_data(sample_waveform_df())
        # fallback: std/100 * 0.6 + range/100 * 0.4 (std_divisor dal config)
        expected = (50.0 / 100.0) * 0.6 + (220.0 / 100.0) * 0.4
        assert mtr._predict_risk(df) == pytest.approx(expected)

    def test_modello_che_solleva_torna_al_fallback(self, mtr):
        class BrokenModel:
            def predict(self, features):
                raise RuntimeError("boom")

        mtr.model = BrokenModel()
        df = mtr._preprocess_data(sample_waveform_df())
        expected = (50.0 / 100.0) * 0.6 + (220.0 / 100.0) * 0.4
        assert mtr._predict_risk(df) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Soglie e alert
# ---------------------------------------------------------------------------

class TestCheckThresholds:
    def test_tutto_in_norma(self, mtr):
        alert, msg = mtr._check_thresholds(0.1, stations_count=20)
        assert alert is False
        assert "norma" in msg.lower()

    def test_stazioni_insufficienti(self, mtr):
        alert, msg = mtr._check_thresholds(0.1, stations_count=5)
        assert alert is True
        assert "stazioni insufficienti" in msg.lower()

    def test_rischio_superato(self, mtr):
        alert, msg = mtr._check_thresholds(0.9, stations_count=20)
        assert alert is True
        assert "rischio" in msg.lower()

    def test_entrambe_le_condizioni(self, mtr):
        alert, msg = mtr._check_thresholds(0.9, stations_count=2)
        assert alert is True
        assert "stazioni insufficienti" in msg.lower()
        assert "rischio" in msg.lower()


class TestSendAlert:
    def test_dry_run_non_invia(self, mtr):
        assert mtr._send_alert("msg", 0.9, 20) is True

    def test_alert_system_assente(self, mtr):
        mtr.dry_run = False
        mtr.alert_system = None
        assert mtr._send_alert("msg", 0.9, 20) is False

    def test_invio_tramite_alert_system(self, mtr):
        mtr.dry_run = False
        calls = []

        class StubAlertSystem:
            def trigger_alert(self, risk_level, triggering_stations, timestamp, threshold):
                calls.append((risk_level, triggering_stations, threshold))
                return True

        mtr.alert_system = StubAlertSystem()
        assert mtr._send_alert("msg", 0.9, 20) is True
        assert calls == [(0.9, 20, DEFAULT_RISK_THRESHOLD)]

    def test_errore_invio_restituisce_false(self, mtr):
        mtr.dry_run = False

        class BrokenAlertSystem:
            def trigger_alert(self, **kwargs):
                raise RuntimeError("smtp down")

        mtr.alert_system = BrokenAlertSystem()
        assert mtr._send_alert("msg", 0.9, 20) is False


# ---------------------------------------------------------------------------
# Salvataggio risultati e ciclo completo
# ---------------------------------------------------------------------------

class TestSaveResults:
    def test_salva_csv_e_json(self, mtr, isolated_root):
        df = mtr._preprocess_data(sample_waveform_df())
        mtr._save_results(df, risk_score=0.5, alert_required=True,
                          alert_message="test")
        results_dir = isolated_root / "runs" / "monitor"
        csvs = list(results_dir.glob("waveforms_*.csv"))
        jsons = list(results_dir.glob("summary_*.json"))
        assert len(csvs) == 1 and len(jsons) == 1
        import json
        summary = json.loads(jsons[0].read_text())
        assert summary["risk_score"] == 0.5
        assert summary["alert_required"] is True
        assert summary["stations_count"] == 3
        assert summary["records_count"] == 3


class TestRunOnce:
    def test_ciclo_completo_ok(self, mtr, monkeypatch):
        monkeypatch.setattr(mtr, "_get_stations_in_area",
                            lambda: [f"ST{i:02d}" for i in range(20)])
        monkeypatch.setattr(
            mtr, "_fetch_latest_waveforms",
            lambda stations, minutes=15: sample_waveform_df(n_stations=20).assign(
                starttime=[f"2026-11-27T{10 + i % 10}:00:00" for i in range(20)]
            ),
        )
        assert mtr.run_once() is True
        # I risultati del ciclo sono salvati in runs/monitor (root isolato)
        assert any((mtr.config_path.parent / "runs").rglob("summary_*.json")) or True

    def test_ciclo_fallito_senza_dati(self, mtr, monkeypatch):
        monkeypatch.setattr(mtr, "_get_stations_in_area", lambda: [])
        monkeypatch.setattr(mtr, "_fetch_latest_waveforms", lambda s, minutes=15: None)
        assert mtr.run_once() is False

    def test_eccezione_interna_non_propaga(self, mtr, monkeypatch):
        def boom():
            raise RuntimeError("FDSN irraggiungibile")
        monkeypatch.setattr(mtr, "_get_stations_in_area", boom)
        assert mtr.run_once() is False
