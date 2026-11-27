"""
Test per ingest_runs_to_db.py — ingestione delle run nel database DuckDB.

Strategia di test:
- Ogni test usa un database DuckDB temporaneo (tmp_path) tramite monkeypatch
  di ingest_runs_to_db.DUCKDB_PATH, così da non toccare il DB reale.
- Le run "finte" vengono create su disco con i CSV attesi dal modulo
  (interim/station_deltas_from_mseed.csv, processed/station_stats.csv,
  processed/deltas_spatial.csv).
- Si verifica il contenuto del DB leggendo le tabelle con duckdb.
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ingest_runs_to_db as ingest


# ---------------------------------------------------------------------------
# Fixture di supporto
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Punta DUCKDB_PATH su un file temporaneo e restituisce il percorso."""
    db_path = tmp_path / "test_seismic.duckdb"
    monkeypatch.setattr(ingest, "DUCKDB_PATH", db_path)
    return db_path


def make_run_dir(tmp_path, with_deltas=True, with_stats=True, with_spatial=True):
    """Crea una directory di run con i CSV attesi dal modulo."""
    run_dir = tmp_path / "run_test"
    (run_dir / "interim").mkdir(parents=True)
    (run_dir / "processed").mkdir(parents=True)

    if with_deltas:
        pd.DataFrame({
            "event_id": ["ev1", "ev1", "ev2"],
            "network": ["IV", "IV", "IV"],
            "station": ["ST01", "ST02", "ST01"],
            "channel": ["HHZ", "HHZ", "HHZ"],
            "arrival_epoch": [1700000000.0, 1700000000.1, 1700003600.0],
            "arrival_iso": [
                "2023-11-14T22:13:20", "2023-11-14T22:13:20", "2023-11-14T23:13:20",
            ],
            "event_reference_epoch": [1699999900.0, 1699999900.0, 1700003500.0],
            "delta_seconds": [0.5, -0.2, 0.8],
        }).to_csv(run_dir / "interim" / "station_deltas_from_mseed.csv", index=False)

    if with_stats:
        pd.DataFrame({
            "station": ["ST01", "ST02"],
            "base_count": [10, 12],
            "base_mean": [0.4, -0.1],
            "base_std": [0.1, 0.2],
            "base_median": [0.4, -0.1],
            "soft_count": [8, 9],
            "soft_mean": [0.5, 0.0],
            "soft_std": [0.1, 0.2],
            "soft_median": [0.5, 0.0],
            "soft_minus_base_mean": [0.1, 0.1],
        }).to_csv(run_dir / "processed" / "station_stats.csv", index=False)

    if with_spatial:
        pd.DataFrame({
            "station": ["ST01", "ST02"],
            "network": ["IV", "IV"],
            "latitude": [40.8, 40.9],
            "longitude": [14.1, 14.2],
            "easting": [430000.0, 431000.0],
            "northing": [4520000.0, 4521000.0],
            "elevation": [100.0, 150.0],
            "location": ["Solfatara", "Pozzuoli"],
            "base_mean": [0.4, -0.1],
        }).to_csv(run_dir / "processed" / "deltas_spatial.csv", index=False)

    return run_dir


def read_table(db_path, table):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(f"SELECT * FROM {table}").df()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Test unitari puri
# ---------------------------------------------------------------------------

class TestGetFileHash:
    def test_hash_file_esistente(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("contenuto")
        h = ingest.get_file_hash(f)
        assert len(h) == 64  # SHA256 esadecimale
        # Deterministico
        assert h == ingest.get_file_hash(f)

    def test_hash_file_diversi(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("uno")
        f2.write_text("due")
        assert ingest.get_file_hash(f1) != ingest.get_file_hash(f2)

    def test_hash_file_inesistente(self, tmp_path):
        assert ingest.get_file_hash(tmp_path / "missing.txt") == "N/A"


class TestInitializeDbSchema:
    def test_crea_tutte_le_tabelle(self, temp_db):
        con = duckdb.connect(str(temp_db))
        try:
            ingest.initialize_db_schema(con)
            tables = set(con.execute("SHOW TABLES").df().iloc[:, 0])
            attese = {
                "runs", "stations", "raw_deltas", "station_stats",
                "deltas_spatial", "anomalies", "ml_features_timeseries",
            }
            assert attese <= tables
        finally:
            con.close()

    def test_schema_idempotente(self, temp_db):
        """Chiamare initialize_db_schema due volte non deve dare errori."""
        con = duckdb.connect(str(temp_db))
        try:
            ingest.initialize_db_schema(con)
            ingest.initialize_db_schema(con)  # CREATE TABLE IF NOT EXISTS
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Test di integrazione su ingest_run_data
# ---------------------------------------------------------------------------

class TestIngestRunData:
    def test_ingest_completo(self, temp_db, tmp_path):
        run_dir = make_run_dir(tmp_path)
        ingest.ingest_run_data(
            run_id="run-001", run_name="test_run", run_dir=run_dir,
            source_type="mseed",
        )
        assert temp_db.exists()

        runs = read_table(temp_db, "runs")
        assert len(runs) == 1
        assert runs.iloc[0]["run_id"] == "run-001"
        assert runs.iloc[0]["source_type"] == "mseed"

        raw = read_table(temp_db, "raw_deltas")
        assert len(raw) == 3
        assert set(raw["station_code"]) == {"ST01", "ST02"}
        assert set(raw["run_id"]) == {"run-001"}

        stats = read_table(temp_db, "station_stats")
        assert len(stats) == 2

        stations = read_table(temp_db, "stations")
        assert set(stations["station_code"]) == {"ST01", "ST02"}
        loc_by_station = dict(zip(stations["station_code"], stations["location"]))
        assert loc_by_station["ST01"] == "Solfatara"
        assert loc_by_station["ST02"] == "Pozzuoli"

        spatial = read_table(temp_db, "deltas_spatial")
        assert len(spatial) == 2
        assert set(spatial["delta_type"]) == {"base_mean"}

    def test_ingest_idempotente(self, temp_db, tmp_path):
        """Reingerire la stessa run non deve duplicare i dati (ON CONFLICT DO NOTHING)."""
        run_dir = make_run_dir(tmp_path)
        for _ in range(2):
            ingest.ingest_run_data(
                run_id="run-001", run_name="test_run", run_dir=run_dir,
                source_type="mseed",
            )
        raw = read_table(temp_db, "raw_deltas")
        stats = read_table(temp_db, "station_stats")
        assert len(raw) == 3
        assert len(stats) == 2
        assert len(read_table(temp_db, "runs")) == 1

    def test_ingest_run_dir_vuota(self, temp_db, tmp_path):
        """Una run senza CSV non deve crashare: il DB viene comunque inizializzato."""
        run_dir = tmp_path / "run_vuota"
        run_dir.mkdir()
        ingest.ingest_run_data(
            run_id="run-empty", run_name="vuota", run_dir=run_dir,
            source_type="mseed",
        )
        assert len(read_table(temp_db, "runs")) == 0
        assert len(read_table(temp_db, "raw_deltas")) == 0

    def test_ingest_solo_deltas(self, temp_db, tmp_path):
        run_dir = make_run_dir(tmp_path, with_stats=False, with_spatial=False)
        ingest.ingest_run_data(
            run_id="run-d", run_name="d", run_dir=run_dir, source_type="mseed",
        )
        assert len(read_table(temp_db, "raw_deltas")) == 3
        assert len(read_table(temp_db, "stations")) == 0

    def test_config_hash_registrato(self, temp_db, tmp_path):
        run_dir = make_run_dir(tmp_path)
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: value")
        ingest.ingest_run_data(
            run_id="run-cfg", run_name="cfg", run_dir=run_dir,
            source_type="mseed", config_path=cfg,
        )
        runs = read_table(temp_db, "runs")
        assert runs.iloc[0]["config_hash"] == ingest.get_file_hash(cfg)
        assert runs.iloc[0]["raw_deltas_hash"] != "N/A"


class TestMlView:
    def test_vista_creata_e_interrogabile(self, temp_db, tmp_path):
        run_dir = make_run_dir(tmp_path)
        ingest.ingest_run_data(
            run_id="run-ml", run_name="ml", run_dir=run_dir, source_type="mseed",
        )
        con = duckdb.connect(str(temp_db), read_only=True)
        try:
            df = con.execute("SELECT * FROM ml_features_ready_view").df()
            # Vista valida: le colonne chiave del feature engineering ci sono
            for col in ("timestamp", "numero_eventi", "energia_max", "Target_Allarme"):
                assert col in df.columns
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Test della CLI (main)
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_argparse(self, temp_db, tmp_path, monkeypatch, capsys):
        run_dir = make_run_dir(tmp_path)
        monkeypatch.setattr(
            sys, "argv",
            ["ingest_runs_to_db.py", "--run-id", "cli-1", "--run-name", "cli",
             "--run-dir", str(run_dir), "--source-type", "mseed"],
        )
        ingest.main()
        assert len(read_table(temp_db, "runs")) == 1
        assert read_table(temp_db, "runs").iloc[0]["run_id"] == "cli-1"

    def test_main_missing_required_args(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ingest_runs_to_db.py"])
        with pytest.raises(SystemExit):
            ingest.main()


class TestIngestAttachCoordsSchema:
    """Regressione: attach_coords_to_deltas.py produce deltas_spatial.csv
    con colonne x_m/y_m (senza network/elevation/location) e l'ingest
    crashava con KeyError. Verificata su run reale in Colab (2026-11-27).
    """

    def _make_attach_coords_run(self, tmp_path):
        run_dir = tmp_path / "run_attach_coords"
        (run_dir / "processed").mkdir(parents=True)
        pd.DataFrame({
            # Schema reale prodotto da attach_coords_to_deltas.py
            "station": ["CAAM", "CAFL"],
            "latitude": [40.82, 40.83],
            "longitude": [14.14, 14.15],
            "x_m": [430000.0, 431000.0],
            "y_m": [4520000.0, 4521000.0],
            "base_mean": [0.4, -0.1],
        }).to_csv(run_dir / "processed" / "deltas_spatial.csv", index=False)
        return run_dir

    def test_ingest_schema_attach_coords(self, temp_db, tmp_path):
        run_dir = self._make_attach_coords_run(tmp_path)
        ingest.ingest_run_data(
            run_id="run-attach", run_name="attach", run_dir=run_dir,
            source_type="catalog",
        )
        # Le stazioni sono ingerite con easting/northing mappati da x_m/y_m
        stations = read_table(temp_db, "stations")
        assert set(stations["station_code"]) == {"CAAM", "CAFL"}
        coords = {
            row["station_code"]: (row["easting"], row["northing"])
            for _, row in stations.iterrows()
        }
        assert coords["CAAM"] == (430000.0, 4520000.0)
        assert coords["CAFL"] == (431000.0, 4521000.0)
        # Colonne opzionali mancanti -> NULL accettati dallo schema
        # deltas_spatial ingerito con i valori base_mean
        spatial = read_table(temp_db, "deltas_spatial")
        assert len(spatial) == 2
        assert set(spatial["delta_type"]) == {"base_mean"}
