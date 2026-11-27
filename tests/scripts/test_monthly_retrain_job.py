"""
Test per scripts/monthly_retrain_job.py — job mensile di riaddestramento (issue #7).

Il job è un wrapper di subprocess: si testa che:
- run_command gestisca successo e fallimento (CalledProcessError)
- main esca con errore se il dataset ML manca
- main lanci il comando di training corretto (subprocess.run mockato)
"""
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def load_job():
    spec = importlib.util.spec_from_file_location(
        "monthly_retrain_job", SCRIPTS_DIR / "monthly_retrain_job.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def job():
    return load_job()


class TestRunCommand:
    def test_comando_riuscito(self, job, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, check: calls.append(cmd))
        assert job.run_command(["python", "train.py"], "errore") is True
        assert calls == [["python", "train.py"]]

    def test_comando_fallito(self, job, monkeypatch):
        def boom(cmd, check):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "run", boom)
        assert job.run_command(["python", "train.py"], "errore") is False


class TestMain:
    def test_dataset_mancante_esce_con_errore(self, job, monkeypatch, tmp_path):
        # PROJECT_ROOT del job puntato su una root senza dataset
        monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)
        with pytest.raises(SystemExit) as exc:
            job.main()
        assert exc.value.code == 1

    def test_dataset_presente_lancia_training(self, job, monkeypatch, tmp_path):
        dataset = tmp_path / "examples" / "mobile_devices" / "dataset_ml_sismico.csv"
        dataset.parent.mkdir(parents=True)
        dataset.write_text("a,b,target\n1,2,3\n")
        monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)

        mock_run = MagicMock()
        monkeypatch.setattr(subprocess, "run", mock_run)

        # Non deve sollevare SystemExit
        job.main()

        # Il comando di training è stato invocato una volta, con gli argomenti chiave
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "train_modello.py" in cmd[1]
        assert "--dataset" in cmd and str(dataset) in cmd
        assert "--model-output-dir" in cmd
        assert str(tmp_path / "mobile" / "models") in cmd
        assert "--model-type" in cmd
        assert "compare" in cmd

    def test_training_fallito_interrompe_job(self, job, monkeypatch, tmp_path):
        dataset = tmp_path / "examples" / "mobile_devices" / "dataset_ml_sismico.csv"
        dataset.parent.mkdir(parents=True)
        dataset.write_text("a,b,target\n1,2,3\n")
        monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)

        def boom(cmd, check):
            raise subprocess.CalledProcessError(2, cmd)

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(SystemExit) as exc:
            job.main()
        assert exc.value.code == 1
