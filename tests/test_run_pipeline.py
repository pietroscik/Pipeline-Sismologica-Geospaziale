"""
Test unitari per le funzioni helper di run_pipeline.py (orchestratore).

Non si testa qui l'intera pipeline (coperta da tests/integration/test_pipeline.py
via subprocess): si testano le unità riutilizzabili:
- run_cmd: esecuzione comandi esterni con timeout e gestione errori
- resolve_input_path: risoluzione path assoluti/progetto-relativi
- validate_csv_input: validazione CSV in ingresso
- cleanup_run_directory / setup_run_directory: gestione directory di run
"""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import run_pipeline as rp


# ---------------------------------------------------------------------------
# run_cmd
# ---------------------------------------------------------------------------

class TestRunCmd:
    def test_comando_riuscito(self):
        assert rp.run_cmd([sys.executable, "-c", "print('ok')"]) is True

    def test_comando_fallito_solleva(self):
        with pytest.raises(subprocess.CalledProcessError):
            rp.run_cmd([sys.executable, "-c", "import sys; sys.exit(1)"])

    def test_comando_fallito_opzionale(self):
        """Con optional=True il fallimento restituisce False senza sollevare."""
        assert rp.run_cmd(
            [sys.executable, "-c", "import sys; sys.exit(1)"], optional=True
        ) is False

    def test_timeout_solleva(self):
        with pytest.raises(subprocess.TimeoutExpired):
            rp.run_cmd([sys.executable, "-c", "import time; time.sleep(5)"],
                       timeout=1)

    def test_timeout_opzionale(self):
        assert rp.run_cmd(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            optional=True, timeout=1,
        ) is False

    def test_path_come_argomento(self, tmp_path):
        """Gli argomenti Path vengono convertiti in stringhe senza errori."""
        script = tmp_path / "flag.txt"
        assert rp.run_cmd([sys.executable, "-c",
                           f"open(r'{script}', 'w').write('x')"]) is True
        assert script.exists()

    def test_cwd(self, tmp_path):
        assert rp.run_cmd([sys.executable, "-c", "print('ok')"], cwd=str(tmp_path)) is True


# ---------------------------------------------------------------------------
# resolve_input_path
# ---------------------------------------------------------------------------

class TestResolveInputPath:
    def test_none_restituisce_none(self):
        assert rp.resolve_input_path(None, [Path("/tmp")]) is None

    def test_stringa_vuota_restituisce_none(self):
        assert rp.resolve_input_path("", [Path("/tmp")]) is None

    def test_path_assoluto_esistente(self, tmp_path):
        f = tmp_path / "file.csv"
        f.write_text("x")
        assert rp.resolve_input_path(str(f), []) == f

    def test_path_assoluto_mancante_solleva(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rp.resolve_input_path(str(tmp_path / "missing.csv"), [])

    def test_path_progetto_relativo(self, tmp_path, monkeypatch):
        """Un path relativo esistente sotto PROJECT_ROOT viene risolto."""
        fake_root = tmp_path / "root"
        (fake_root / "data" / "raw").mkdir(parents=True)
        (fake_root / "data" / "raw" / "events.csv").write_text("x")
        monkeypatch.setattr(rp, "PROJECT_ROOT", fake_root)
        result = rp.resolve_input_path("data/raw/events.csv", [])
        assert result == fake_root / "data" / "raw" / "events.csv"

    def test_fallback_dirs(self, tmp_path, monkeypatch):
        """Se non è nel PROJECT_ROOT, si cerca nelle fallback_dirs."""
        fake_root = tmp_path / "root"
        fake_root.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (fallback / "input.csv").write_text("x")
        monkeypatch.setattr(rp, "PROJECT_ROOT", fake_root)
        assert rp.resolve_input_path("input.csv", [fallback]) == fallback / "input.csv"


# ---------------------------------------------------------------------------
# validate_csv_input
# ---------------------------------------------------------------------------

class TestValidateCsvInput:
    def test_none_restituisce_none(self):
        assert rp.validate_csv_input(None) is None

    def test_csv_valido(self, tmp_path):
        csv = tmp_path / "stations.csv"
        pd.DataFrame({"station": ["ST01"], "latitude": [40.8]}).to_csv(csv, index=False)
        df = rp.validate_csv_input(csv, required_columns={"station", "latitude"},
                                   path_description="Stazioni")
        assert df is not None and len(df) == 1

    def test_colonne_mancanti_solleva(self, tmp_path):
        csv = tmp_path / "stations.csv"
        pd.DataFrame({"station": ["ST01"]}).to_csv(csv, index=False)
        from mobile.data_validator import DataValidationError
        with pytest.raises(DataValidationError):
            rp.validate_csv_input(csv, required_columns={"latitude"})

    def test_file_inesistente_solleva(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rp.validate_csv_input(tmp_path / "missing.csv")


# ---------------------------------------------------------------------------
# setup_run_directory / cleanup_run_directory
# ---------------------------------------------------------------------------

class TestSetupRunDirectory:
    def test_crea_struttura(self, tmp_path):
        run_dir = tmp_path / "runs" / "mia_run"
        interim, processed, maps, mobile = rp.setup_run_directory(run_dir)
        assert run_dir.is_dir()
        for d in (interim, processed, maps, mobile):
            assert d.is_dir()
            assert d.parent == run_dir
        assert interim.name == "interim"
        assert processed.name == "processed"
        assert maps.name == "maps"
        assert mobile.name == "mobile_analysis"

    def test_idempotente(self, tmp_path):
        run_dir = tmp_path / "run"
        rp.setup_run_directory(run_dir)
        rp.setup_run_directory(run_dir)  # esist_ok=True: nessuna eccezione
        assert run_dir.is_dir()


class TestCleanupRunDirectory:
    def test_pulisce_directory_esistente(self, tmp_path):
        run_dir = tmp_path / "run"
        (run_dir / "interim").mkdir(parents=True)
        (run_dir / "interim" / "f.csv").write_text("x")
        rp.cleanup_run_directory(run_dir)
        assert not run_dir.exists()

    def test_directory_inesistente_nessun_errore(self, tmp_path):
        rp.cleanup_run_directory(tmp_path / "non_esiste")  # non deve sollevare


# ---------------------------------------------------------------------------
# Regressioni leggere sull'orchestratore
# ---------------------------------------------------------------------------

class TestOrchestratorContract:
    def test_help_stampa_nome_pipeline(self):
        """Il messaggio di help deve contenere 'Pipeline' (contratto dei test di integrazione)."""
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "run_pipeline.py"), "--help"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0
        assert "Pipeline" in result.stdout + result.stderr

    def test_input_inesistente_termina_con_errore(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "run_pipeline.py"),
             "--run-name", "test_invalid", "--delta-csv", "/nonexistent/file.csv"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode != 0
