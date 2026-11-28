"""
Test per i fix della run reale 2026-11-28:

1. Selezione best model (PR-AUC primario, tie-break MCC e priorita' fissa):
   con il vecchio criterio (test_f1_score con '>' stretto) i pareggi a 0.0
   selezionavano sempre il primo modello addestrato (XGBoost, il peggiore).
2. Parallelizzazione di compute_mseed_deltas: process_file produce gli
   stessi trigger sia in sequenziale che in ProcessPoolExecutor.
3. Flag CLI --workers (compute_mseed_deltas) e --mseed-workers (run_pipeline).
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from compute_mseed_deltas import PickConfig, process_file  # noqa: E402
from train_risk_model import select_best_model  # noqa: E402


class TestSelectBestModel:
    """Test della selezione deterministica del best model."""

    def test_pareggi_f1_seleziona_transformer_non_xgboost(self):
        # Caso reale 2026-11-28: tutti con F1=0.0, il vecchio criterio
        # ('>' stretto sul primo) selezionava XGBoost (il peggiore).
        candidates = [
            ("xgboost", object(), {"test_f1_score": 0.0, "test_average_precision": 0.03, "test_mcc": -0.09}),
            ("random_forest", object(), {"test_f1_score": 0.0, "test_average_precision": 0.03, "test_mcc": 0.0}),
            ("transformer", object(), {"test_f1_score": 0.0, "test_average_precision": 0.5, "test_mcc": 0.0}),
        ]
        name, model = select_best_model(candidates)
        assert name == "transformer"
        assert model is candidates[2][1]

    def test_pareggi_totali_priorita_fissa(self):
        # Tutte le metriche pari: vince transformer (priorita' fissa), non il
        # primo della lista.
        candidates = [
            ("xgboost", object(), {"test_average_precision": 0.0, "test_mcc": 0.0}),
            ("transformer", object(), {"test_average_precision": 0.0, "test_mcc": 0.0}),
            ("random_forest", object(), {"test_average_precision": 0.0, "test_mcc": 0.0}),
        ]
        name, _ = select_best_model(candidates)
        assert name == "transformer"

    def test_mcc_tiebreak(self):
        candidates = [
            ("transformer", object(), {"test_average_precision": 0.4, "test_mcc": 0.10}),
            ("random_forest", object(), {"test_average_precision": 0.4, "test_mcc": 0.25}),
        ]
        name, _ = select_best_model(candidates)
        assert name == "random_forest"

    def test_pr_auc_vince_su_mcc(self):
        # PR-AUC e' primario: batte anche un MCC alto.
        candidates = [
            ("transformer", object(), {"test_average_precision": 0.6, "test_mcc": 0.05}),
            ("xgboost", object(), {"test_average_precision": 0.2, "test_mcc": 0.50}),
        ]
        name, _ = select_best_model(candidates)
        assert name == "transformer"

    def test_mcc_nan_gestito(self):
        # matthews_corrcoef restituisce NaN con una sola classe nel test.
        candidates = [
            ("xgboost", object(), {"test_average_precision": 0.1, "test_mcc": np.nan}),
            ("transformer", object(), {"test_average_precision": 0.1, "test_mcc": 0.0}),
        ]
        name, _ = select_best_model(candidates)
        assert name == "transformer"

    def test_lista_vuota(self):
        name, model = select_best_model([])
        assert name == ""
        assert model is None

    def test_metriche_mancanti_default_zero(self):
        candidates = [
            ("xgboost", object(), {}),
            ("random_forest", object(), {"test_average_precision": 0.01}),
        ]
        name, _ = select_best_model(candidates)
        assert name == "random_forest"


obspy = pytest.importorskip("obspy")
from obspy import Stream, Trace, UTCDateTime  # noqa: E402


def _write_synthetic_mseed(directory: Path, n_files: int = 4) -> list[Path]:
    """Crea n_files MiniSEED sintetici con un transiente netto."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n_files):
        tr = Trace(
            data=np.zeros(3600 * 100, dtype=np.int32),  # 1 ora a 100 Hz
            header={
                "network": "IV",
                "station": f"ST{i:02d}",
                "channel": "HHZ",
                "sampling_rate": 100.0,
                "starttime": UTCDateTime(2026, 11, 1, i),
            },
        )
        # Transiente ampio a metà traccia: deve superare la soglia STA/LTA
        tr.data[180000:180500] = 5000
        path = directory / f"ev{i}_IV_ST{i:02d}_HHZ.mseed"
        tr.write(str(path), format="MSEED")
        paths.append(path)
    return paths


@pytest.fixture
def synthetic_mseed(tmp_path):
    return _write_synthetic_mseed(tmp_path / "waveforms")


class TestProcessFileParallel:
    """Equivalenza parallelo/sequenziale dell'elaborazione STA/LTA."""

    def test_process_file_trova_trigger(self, synthetic_mseed):
        cfg = PickConfig()
        rows, energy = process_file(synthetic_mseed[0], cfg)
        assert rows, "Il transiente sintetico deve generare almeno un trigger"
        assert energy, "La statistica energetica oraria non deve essere vuota"
        row = rows[0]
        assert row["station"] == "ST00"
        assert row["network"] == "IV"
        assert row["channel"] == "HHZ"
        assert row["filename"] == str(synthetic_mseed[0])

    def test_parallelo_uguale_a_sequenziale(self, synthetic_mseed):
        from concurrent.futures import ProcessPoolExecutor

        cfg = PickConfig()
        seq, seq_energy = [], []
        for p in synthetic_mseed:
            p_rows, p_energy = process_file(p, cfg)
            seq.extend(p_rows)
            seq_energy.extend(p_energy)
        with ProcessPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                process_file, synthetic_mseed, [cfg] * len(synthetic_mseed)
            ))
        par = [r for rows, _e in results for r in rows]
        par_energy = [e for _r, es in results for e in es]
        key = lambda r: (r["filename"], r["arrival_epoch"])  # noqa: E731
        assert sorted(seq, key=key) == sorted(par, key=key)
        ekey = lambda e: (e["filename"], e["window_epoch"])  # noqa: E731
        assert sorted(seq_energy, key=ekey) == sorted(par_energy, key=ekey)

    def test_process_file_energia_rms_e_picco(self, synthetic_mseed):
        """RMS e picco catturano il transiente sintetico (5000 counts)."""
        cfg = PickConfig()
        _rows, energy = process_file(synthetic_mseed[0], cfg)
        assert energy, "Il CSV energetico deve avere una riga per finestra oraria"
        e = energy[0]
        assert set(e) >= {"station", "channel", "window_epoch", "rms", "peak"}
        assert e["rms"] > 0.0
        assert e["peak"] > 1000.0, "Il picco deve riflettere il transiente (5000 counts)"

    def test_file_ileggibile_restituisce_lista_vuota(self, tmp_path):
        bad = tmp_path / "ev9_IV_ST09_HHZ.mseed"
        bad.write_bytes(b"not a mseed")
        assert process_file(bad, PickConfig()) == ([], [])


class TestCliFlags:
    """Flag CLI aggiunti nella PR."""

    def test_compute_mseed_deltas_workers_flag(self):
        cmd = [
            sys.executable, "-m", "scripts.compute_mseed_deltas", "--help",
        ]
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0
        assert "--workers" in result.stdout

    def test_run_pipeline_mseed_workers_flag(self):
        cmd = [sys.executable, str(PROJECT_ROOT / "run_pipeline.py"), "--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0
        assert "--mseed-workers" in result.stdout or "--mseed-workers" in result.stderr
