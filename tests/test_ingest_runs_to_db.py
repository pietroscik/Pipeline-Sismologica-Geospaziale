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


class TestDataCompleteness:
    """Test della colonna is_data_complete e della griglia oraria continua
    (ore mancanti generate esplicitamente con flag 0)."""

    def _make_multi_day_run(self, tmp_path):
        """Run su più giorni: giorno 1 tutte le stazioni (completo),
        giorno 2 una sola stazione (incompleto), giorno 4 ultima attività."""
        run_dir = tmp_path / "run_completeness"
        (run_dir / "interim").mkdir(parents=True)
        rows = []
        # Giorno 1: 3 stazioni attive -> completezza 1.0
        for st in ("ST01", "ST02", "ST03"):
            rows.append({
                "event_id": f"ev1-{st}", "network": "IV", "station": st,
                "channel": "HHZ", "arrival_epoch": 1672531200.0,
                "arrival_iso": "2023-01-01T12:00:00",
                "event_reference_epoch": 1672531100.0, "delta_seconds": 1.0,
            })
        # Giorno 2: solo 1 stazione su 3 -> completezza 0.33 -> flag 0
        rows.append({
            "event_id": "ev2-ST01", "network": "IV", "station": "ST01",
            "channel": "HHZ", "arrival_epoch": 1672617600.0,
            "arrival_iso": "2023-01-02T12:00:00",
            "event_reference_epoch": 1672617500.0, "delta_seconds": 2.0,
        })
        # Giorno 4: di nuovo tutte (spinge il max per la griglia)
        for st in ("ST01", "ST02", "ST03"):
            rows.append({
                "event_id": f"ev4-{st}", "network": "IV", "station": st,
                "channel": "HHZ", "arrival_epoch": 1672876800.0,
                "arrival_iso": "2023-01-04T12:00:00",
                "event_reference_epoch": 1672876700.0, "delta_seconds": 3.0,
            })
        pd.DataFrame(rows).to_csv(
            run_dir / "interim" / "station_deltas_from_mseed.csv", index=False)
        return run_dir

    def test_flag_completezza_e_ore_mancanti(self, temp_db, tmp_path):
        run_dir = self._make_multi_day_run(tmp_path)
        ingest.ingest_run_data(
            run_id="run-comp", run_name="comp", run_dir=run_dir,
            source_type="mseed",
        )
        df = read_table(temp_db, "ml_features_ready_view")

        # La colonna esiste
        assert "is_data_complete" in df.columns

        # Le ore mancanti (es. giorno 3 senza dati) sono righe esplicite:
        # numero_eventi=0 e flag 0
        day3 = df[pd.to_datetime(df["timestamp"]).dt.date.astype(str) == "2023-01-03"]
        # 24 ore: dal target da catalogo (2026-11-28) il boundary esclude solo
        # le ultime 6h (max dati = 4 gen 12:00 -> incluse le ore < 4 gen 06:00,
        # cioè il giorno 3 completo)
        assert len(day3) == 24
        assert (day3["numero_eventi"] == 0).all()
        assert (day3["is_data_complete"] == 0).all()

        # Giorno 1, ora 12: tutte e 3 le stazioni attive -> flag 1
        d1 = df[(pd.to_datetime(df["timestamp"]).dt.date.astype(str) == "2023-01-01")
                & (pd.to_datetime(df["timestamp"]).dt.hour == 12)]
        assert d1["is_data_complete"].iloc[0] == 1
        assert d1["numero_eventi"].iloc[0] == 3

        # Giorno 2, ora 12: 1 stazione su 3 (33% < 95%) -> flag 0
        d2 = df[(pd.to_datetime(df["timestamp"]).dt.date.astype(str) == "2023-01-02")
                & (pd.to_datetime(df["timestamp"]).dt.hour == 12)]
        assert d2["is_data_complete"].iloc[0] == 0
        assert d2["numero_eventi"].iloc[0] == 1

        # Le ore senza dati compaiono anche dentro un giorno con dati:
        # l'ora 15 del giorno 1 è nella griglia (dopo il min 12:00) ma senza
        # rilevamenti -> riga esplicita con flag 0
        d1_senza = df[(pd.to_datetime(df["timestamp"]).dt.date.astype(str) == "2023-01-01")
                      & (pd.to_datetime(df["timestamp"]).dt.hour == 15)]
        assert len(d1_senza) == 1
        assert d1_senza["numero_eventi"].iloc[0] == 0
        assert d1_senza["is_data_complete"].iloc[0] == 0

    def test_storico_con_null(self, temp_db, tmp_path):
        """La parte storica (ml_features_timeseries) ha is_data_complete NULL."""
        run_dir = self._make_multi_day_run(tmp_path)
        ingest.ingest_run_data(
            run_id="run-comp", run_name="comp", run_dir=run_dir,
            source_type="mseed",
        )
        con = duckdb.connect(str(temp_db), read_only=True)
        try:
            # nessuna riga storica nel DB di test: verifichiamo la struttura
            # della vista interrogandola direttamente (colonna sempre presente)
            df = con.execute("SELECT is_data_complete FROM ml_features_ready_view").df()
            assert len(df) > 0
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Target da catalogo eventi INGV (ground truth, 2026-11-28)
# ---------------------------------------------------------------------------

def _make_catalog_run(tmp_path, monkeypatch, events=None):
    """Run con soli delta + catalogo eventi: restituisce (run_dir, db_path)."""
    db_path = tmp_path / "catalog_test.duckdb"
    monkeypatch.setattr(ingest, "DUCKDB_PATH", db_path)
    run_dir = tmp_path / "run_cat"
    (run_dir / "interim").mkdir(parents=True)

    # 3 giorni di delta orari per 2 stazioni (banda ~9s come i dati reali)
    rows = []
    base = pd.Timestamp("2025-11-01")
    for day in range(3):
        for h in range(24):
            for st in ("CAAM", "CAWE"):
                t = base + pd.Timedelta(days=day, hours=h)
                rows.append({
                    "event_id": f"e{day}{h}{st}", "station": st, "network": "IV",
                    "channel": "HHZ", "arrival_epoch": int(t.timestamp()),
                    "arrival_iso": t, "event_reference_epoch": int(t.timestamp()),
                    "delta_seconds": 9.0, "run_id": "run_cat",
                })
    pd.DataFrame(rows).to_csv(
        run_dir / "interim" / "station_deltas_from_mseed.csv", index=False
    )

    if events is not None:
        pd.DataFrame(events).to_csv(
            run_dir / "interim" / "catalog_events.csv", index=False
        )
    return run_dir, db_path


class TestCatalogTarget:
    """Il Target_Allarme deriva dal catalogo INGV, non più da
    max(delta_seconds) > 15.0 (degenere sui dati reali: banda 7.96-9.88s)."""

    EVENTS = [
        {"event_id": "EV1", "time": "2025-11-01T05:30:00",
         "latitude": 40.83, "longitude": 14.13, "depth_km": 2.0,
         "mag_type": "ML", "magnitude": 4.0,
         "event_location_name": "Campi Flegrei", "event_type": "earthquake"},
        {"event_id": "EV2", "time": "2025-11-02T10:00:00",
         "latitude": 40.83, "longitude": 14.13, "depth_km": 2.0,
         "mag_type": "ML", "magnitude": 2.0,
         "event_location_name": "Campi Flegrei", "event_type": "earthquake"},
    ]

    def test_target_1_per_ore_precedenti_evento_rilevante(self, tmp_path, monkeypatch):
        """M4.0 alle 05:30 del 01: target=1 per le ore 00-05 (orizzonte 6h)."""
        run_dir, db_path = _make_catalog_run(
            tmp_path, monkeypatch, events=self.EVENTS)
        ingest.ingest_run_data("run_cat", "run_cat", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT timestamp, Target_Allarme FROM ml_features_ready_view "
            "ORDER BY timestamp"
        ).df()
        con.close()

        positive = df[df.Target_Allarme == 1]["timestamp"].tolist()
        assert len(positive) == 6
        assert all(t.hour <= 5 and t.day == 1 for t in pd.to_datetime(positive))

    def test_evento_sotto_soglia_non_genera_target(self, tmp_path, monkeypatch):
        """Il M2.0 (soglia 3.5) del 02 non produce alcun positivo quel giorno."""
        run_dir, db_path = _make_catalog_run(
            tmp_path, monkeypatch, events=self.EVENTS)
        ingest.ingest_run_data("run_cat", "run_cat", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT timestamp, Target_Allarme FROM ml_features_ready_view "
            "WHERE timestamp >= TIMESTAMP '2025-11-02' ORDER BY timestamp"
        ).df()
        con.close()
        assert df.Target_Allarme.sum() == 0

    def test_catalogo_assente_target_tutto_zero(self, tmp_path, monkeypatch):
        """Senza catalogo nessun falso positivo: target 0 ovunque."""
        run_dir, db_path = _make_catalog_run(tmp_path, monkeypatch, events=None)
        ingest.ingest_run_data("run_cat", "run_cat", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        tot, pos = con.execute(
            "SELECT count(*), sum(Target_Allarme) FROM ml_features_ready_view"
        ).fetchone()
        con.close()
        assert tot > 0 and pos == 0

    def test_feature_passate_del_catalogo(self, tmp_path, monkeypatch):
        """eventi_catalogo_6h e magnitudo_max_6h guardano solo al PASSATO."""
        run_dir, db_path = _make_catalog_run(
            tmp_path, monkeypatch, events=self.EVENTS)
        ingest.ingest_run_data("run_cat", "run_cat", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT timestamp, eventi_catalogo_6h, magnitudo_max_6h "
            "FROM ml_features_ready_view WHERE timestamp < "
            "TIMESTAMP '2025-11-01 05:00' ORDER BY timestamp"
        ).df()
        con.close()
        # Prima delle 05 (ora dell'evento) le finestre passate sono vuote:
        # nessun leakage del futuro nelle feature
        assert (df.eventi_catalogo_6h == 0).all()
        assert (df.magnitudo_max_6h == 0).all()

    def test_ingest_catalogo_idempotente(self, tmp_path, monkeypatch):
        """Doppia ingest dello stesso catalogo non duplica gli eventi."""
        run_dir, db_path = _make_catalog_run(
            tmp_path, monkeypatch, events=self.EVENTS)
        ingest.ingest_run_data("run_cat", "run_cat", run_dir, "mseed")
        ingest.ingest_run_data("run_cat", "run_cat", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        n = con.execute("SELECT count(*) FROM catalog_events").fetchone()[0]
        con.close()
        assert n == len(self.EVENTS)

    def test_parametri_target_da_config(self, monkeypatch):
        """mag_threshold e horizon_hours arrivano da config.yaml (sezione catalog)."""
        monkeypatch.setattr(
            ingest, "load_config",
            lambda *a, **k: {"catalog": {"mag_threshold": 4.5, "horizon_hours": 3}},
        )
        params = ingest.get_catalog_target_params()
        assert params == {"mag_threshold": 4.5, "horizon_hours": 3}


class TestEnergyFeatures:
    """Feature energetiche orarie (RMS/picco) dalla tabella
    waveform_energy_hourly: l'energia REALE del segnale, assente nei soli
    conteggi di trigger (proxy delta_seconds degenere sui dati reali)."""

    def _make_energy_run(self, tmp_path, monkeypatch, with_energy=True):
        """Run con delta orari + CSV energia: restituisce (run_dir, db_path)."""
        db_path = tmp_path / "energy_test.duckdb"
        monkeypatch.setattr(ingest, "DUCKDB_PATH", db_path)
        run_dir = tmp_path / "run_energy"
        (run_dir / "interim").mkdir(parents=True)

        base = pd.Timestamp("2025-11-01")
        rows = []
        for h in range(24):
            for st in ("CAAM", "CAWE"):
                t = base + pd.Timedelta(hours=h)
                rows.append({
                    "event_id": f"e{h}{st}", "station": st, "network": "IV",
                    "channel": "HHZ", "arrival_epoch": int(t.timestamp()),
                    "arrival_iso": t, "event_reference_epoch": int(t.timestamp()),
                    "delta_seconds": 9.0, "run_id": "run_energy",
                })
        pd.DataFrame(rows).to_csv(
            run_dir / "interim" / "station_deltas_from_mseed.csv", index=False
        )

        if with_energy:
            erows = []
            for h in range(24):
                for st in ("CAAM", "CAWE"):
                    t = base + pd.Timedelta(hours=h)
                    erows.append({
                        "network": "IV", "station": st, "channel": "HHZ",
                        "window_epoch": int(t.timestamp()), "window_iso": t,
                        "rms": 10.0 + h, "peak": 100.0 + h,
                    })
            pd.DataFrame(erows).to_csv(
                run_dir / "interim" / "station_energy_hourly.csv", index=False
            )
        return run_dir, db_path

    def test_tabella_e_ingest_energia(self, tmp_path, monkeypatch):
        run_dir, db_path = self._make_energy_run(tmp_path, monkeypatch)
        ingest.ingest_run_data("run_energy", "run_energy", run_dir, "mseed")

        df = read_table(db_path, "waveform_energy_hourly")
        assert len(df) == 48  # 24 ore x 2 stazioni
        assert set(df.columns) >= {"station_code", "channel", "hour", "rms", "peak"}
        assert df.rms.sum() == pytest.approx(sum(10.0 + h for h in range(24)) * 2)

    def test_ingest_energia_idempotente(self, tmp_path, monkeypatch):
        run_dir, db_path = self._make_energy_run(tmp_path, monkeypatch)
        ingest.ingest_run_data("run_energy", "run_energy", run_dir, "mseed")
        ingest.ingest_run_data("run_energy", "run_energy", run_dir, "mseed")
        df = read_table(db_path, "waveform_energy_hourly")
        assert len(df) == 48  # nessun duplicato

    def test_feature_energetiche_nella_vista(self, tmp_path, monkeypatch):
        """rms_medio/rms_max/picco_max e i rolling energetici sono nella vista."""
        run_dir, db_path = self._make_energy_run(tmp_path, monkeypatch)
        ingest.ingest_run_data("run_energy", "run_energy", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT timestamp, rms_medio, rms_max, picco_max, "
            "rms_max_ultime_6h, rms_max_ultime_24h "
            "FROM ml_features_ready_view ORDER BY timestamp"
        ).df()
        con.close()
        assert not df.empty
        # Il boundary del target esclude le ultime 6 ore (23-6=17): la
        # vista mostra le ore 0-16, quindi rms max = 10+16 = 26
        assert df.rms_medio.max() == pytest.approx(26.0)
        # Il rolling 6h porta il max delle 6 ore precedenti (no futuro)
        assert df.rms_max_ultime_6h.max() == pytest.approx(26.0)

    def test_energia_assente_feature_a_zero(self, tmp_path, monkeypatch):
        """Senza CSV energia la vista resta valida con feature a 0."""
        run_dir, db_path = self._make_energy_run(
            tmp_path, monkeypatch, with_energy=False)
        ingest.ingest_run_data("run_energy", "run_energy", run_dir, "mseed")

        con = duckdb.connect(str(db_path), read_only=True)
        tot, s_rms = con.execute(
            "SELECT count(*), sum(rms_medio) FROM ml_features_ready_view"
        ).fetchone()
        con.close()
        assert tot > 0 and s_rms == 0

    def test_csv_energia_malformato_rifiutato(self, tmp_path, monkeypatch):
        run_dir, db_path = self._make_energy_run(tmp_path, monkeypatch)
        pd.DataFrame({"colonna_sbagliata": [1]}).to_csv(
            run_dir / "interim" / "station_energy_hourly.csv", index=False)
        with pytest.raises(ValueError, match="privo delle colonne"):
            ingest.ingest_run_data("run_energy", "run_energy", run_dir, "mseed")
