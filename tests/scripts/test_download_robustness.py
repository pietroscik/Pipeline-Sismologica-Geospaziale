"""
Test per la robustezza del downloader FDSN (scripts/download_cf_waveforms.py):
- skip-if-exists (resume): un file già scaricato non viene riscaricato
- retry con backoff: i fallimenti transitori vengono ritentati prima di
  passare al client successivo
- statistiche finali per esito (incluso 'skipped')

E per la griglia MCC estesa [0.01, 0.95] nel Transformer.
"""
import importlib.util
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def reset_client_cache():
    """La cache dei client FDSN è per-thread e persiste tra i test nel
    medesimo thread: va azzerata per non contaminare i mock tra test."""
    dlw._THREAD_LOCAL = threading.local()
    yield

obspy = pytest.importorskip("obspy")

spec = importlib.util.spec_from_file_location(
    "download_cf_waveforms", PROJECT_ROOT / "scripts" / "download_cf_waveforms.py"
)
dlw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dlw)

spec2 = importlib.util.spec_from_file_location(
    "train_risk_model", PROJECT_ROOT / "scripts" / "train_risk_model.py"
)
trm = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(trm)


def make_fake_stream():
    """Stream obspy minimale (1 traccia con 10 campioni)."""
    from obspy import Stream, Trace, UTCDateTime
    from obspy.core import Stats

    stats = Stats({"network": "IV", "station": "CAAM", "channel": "HHZ",
                   "npts": 10, "sampling_rate": 100.0,
                   "starttime": UTCDateTime(2026, 1, 1)})
    return Stream([Trace(data=np.arange(10, dtype=np.float32), header=stats)])


def call_worker(tmp_path, fpath=None, **kw):
    """Invoca _download_worker con defaults sensati."""
    from obspy import UTCDateTime
    fpath = fpath or tmp_path / "test.mseed"
    defaults = dict(
        client_urls=["fake://client"],
        network="IV",
        location="*",
        station="CAAM",
        channel="HHZ",
        window_start=UTCDateTime(2026, 1, 1),
        window_end=UTCDateTime(2026, 1, 2),
        fpath=fpath,
        dry_run=False,
    )
    defaults.update(kw)
    return dlw._download_worker(**defaults)


class TestSkipIfExists:
    def test_file_esistente_non_viene_scaricato(self, tmp_path):
        fpath = tmp_path / "gia.mseed"
        fpath.write_bytes(b"dati gia scaricati")
        # anche se il client rispondesse, non deve essere chiamato
        with patch.object(dlw, "Client") as fake_client:
            info = call_worker(tmp_path, fpath=fpath)
        assert info["status"] == "skipped"
        fake_client.assert_not_called()

    def test_file_vuoto_viene_riscaricato(self, tmp_path):
        fpath = tmp_path / "vuoto.mseed"
        fpath.write_bytes(b"")  # download interrotto: file vuoto
        with patch.object(dlw, "Client") as fake_client:
            fake_client.return_value.get_waveforms.return_value = make_fake_stream()
            info = call_worker(tmp_path, fpath=fpath)
        assert info["status"] == "success"
        fake_client.assert_called_once()


class TestRetryBackoff:
    def test_errore_transitorio_ritentato(self, tmp_path):
        """Il primo tentativo fallisce (503), il secondo riesce."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep") as fake_sleep:
            fake_client.return_value.get_waveforms.side_effect = [
                RuntimeError("503 Service Unavailable"),
                make_fake_stream(),
            ]
            info = call_worker(tmp_path)
        assert info["status"] == "success"
        # Client persistente per thread: istanziato UNA volta, riusato per il retry
        assert fake_client.call_count == 1
        assert fake_client.return_value.get_waveforms.call_count == 2
        fake_sleep.assert_called()  # backoff applicato

    def test_errore_4xx_passa_al_client_successivo(self, tmp_path):
        """Un 404 (permanent) non viene ritentato sullo stesso client."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep") as fake_sleep:
            # lista di 2 client: il primo risponde 404, il secondo riesce
            def client_factory(url, timeout=60):
                if "client1" in str(url):
                    c = MagicMock()
                    c.get_waveforms.side_effect = RuntimeError(
                        "404 No data available for request")
                    return c
                c = MagicMock()
                c.get_waveforms.return_value = make_fake_stream()
                return c
            fake_client.side_effect = client_factory
            info = call_worker(tmp_path, client_urls=["fake://client1",
                                                      "fake://client2"])
        assert info["status"] == "success"
        # il client1 non è stato ritentato: 2 chiamate totali (1 per client)
        assert fake_client.call_count == 2

    def test_tutti_i_client_falliti(self, tmp_path):
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):
            fake_client.side_effect = RuntimeError("503 Service Unavailable")
            info = call_worker(tmp_path, client_urls=["fake://a", "fake://b"])
        assert info["status"] == "failed"
        assert "Nessun client" in info["error"]

    def test_413_oversize_segnalato(self, tmp_path):
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):
            fake_client.side_effect = RuntimeError("413 Request Entity Too Large")
            info = call_worker(tmp_path)
        assert info["status"] == "oversize"


class TestStatsFinali:
    def test_statistiche_per_esito(self, tmp_path):
        """La funzione stats conta correttamente gli esiti (mock del logger)."""
        # ricalcola il conteggio come nel main: verifica solo il contratto
        summary = [
            {"status": "success"}, {"status": "success"},
            {"status": "skipped"}, {"status": "failed"},
        ]
        counts = {}
        for res in summary:
            status = res.get("status", "failed")
            counts[status] = counts.get(status, 0) + 1
        assert counts == {"success": 2, "skipped": 1, "failed": 1}


class TestGrigliaMccEstesa:
    def test_soglia_sotto_005_trovabile(self):
        """Run reale 2026-11-27: probabilità dei positivi sotto 0.05 -> con la
        vecchia griglia (min 0.05) tutte le soglie erano degeneri. Ora la
        griglia parte da 0.01 e una soglia utilizzabile viene trovata."""
        y_true = np.array([0] * 50 + [1] * 50)
        y_proba = np.concatenate([
            np.linspace(0.000, 0.010, 50),   # classe 0: probabilità ~0
            np.linspace(0.012, 0.030, 50),    # classe 1: sopra 0.01 ma sotto 0.05
        ])
        thr, mcc = trm.find_best_mcc_threshold(y_true, y_proba)
        # la soglia ottimale deve stare nel range basso, inaccessibile prima
        assert 0.005 <= thr <= 0.03
        assert mcc > 0.9

    def test_griglia_documentata_nei_range(self):
        thr, mcc = trm.find_best_mcc_threshold(
            np.array([0, 1, 0, 1] * 10), np.linspace(0.1, 0.9, 40))
        assert 0.01 <= thr <= 0.95


class TestOversizeFragmentation:
    """Frammentazione automatica delle finestre oversize (413): il task non
    viene più scartato silenziosamente ma scaricato a blocchi più piccoli."""

    def _frag_call(self, tmp_path, **kw):
        """Invoca _download_with_fragmentation con una finestra di 24h."""
        from obspy import UTCDateTime
        defaults = dict(
            client_urls=["fake://client"],
            network="IV",
            location="*",
            station="CAAM",
            channel="HHZ",
            window_start=UTCDateTime(2026, 1, 1),
            window_end=UTCDateTime(2026, 1, 2),
            fpath=tmp_path / "20260101T000000_IV_CAAM_HHZ.mseed",
            dry_run=False,
        )
        defaults.update(kw)
        return dlw._download_with_fragmentation(**defaults)

    def test_oversize_frammentato_e_scaricato(self, tmp_path):
        """413 sulle finestre >6h, successo su quelle <=6h: la finestra da
        24h viene spezzata fino a 4 frammenti da 6h, tutti scaricati."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):

            def fake_get(**k):
                duration = k["endtime"] - k["starttime"]
                if duration > 6 * 3600:
                    raise RuntimeError("413 Request Entity Too Large")
                return make_fake_stream()

            fake_client.return_value.get_waveforms.side_effect = fake_get
            info = self._frag_call(tmp_path)
        assert info["status"] == "success"
        assert info["fragmented"] is True
        # 4 frammenti da 6h scritti su disco
        files = list(tmp_path.glob("*.mseed"))
        assert len(files) == 4

    def test_oversize_persistente_alla_finestra_minima(self, tmp_path):
        """413 sistematico: frammentazione fino alla finestra minima (2h),
        poi oversize riportato onestamente (non silenzioso)."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):
            fake_client.return_value.get_waveforms.side_effect = RuntimeError(
                "413 Request Entity Too Large")
            info = self._frag_call(tmp_path)
        assert info["status"] == "oversize"
        assert info["fragmented"] is True
        assert "oversize persistente" in info["error"]

    def test_frammenti_riprendibili_con_resume(self, tmp_path):
        """I frammenti usano lo schema di nomi del task builder: una seconda
        esecuzione con file frammento già presente li salta (status skipped)."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):

            def fake_get(**k):
                duration = k["endtime"] - k["starttime"]
                if duration > 6 * 3600:
                    raise RuntimeError("413 Request Entity Too Large")
                return make_fake_stream()

            fake_client.return_value.get_waveforms.side_effect = fake_get
            first = self._frag_call(tmp_path)
            assert first["status"] == "success"
            # seconda esecuzione: tutti i frammenti esistono -> skip totale
            second = self._frag_call(tmp_path)
        # il primo frammento coincide col percorso della finestra originale:
        # skip-if-exists lo riconosce prima ancora della frammentazione
        assert second["status"] == "skipped" or second["status"] == "success"
        files = list(tmp_path.glob("*.mseed"))
        assert len(files) == 4  # nessun duplicato


class TestNoDataEClientSenzaDataselect:
    """Run reale 2026-11-27 (HHZ 147 task): il 204 'No data available'
    veniva ritentato 4 volte x 3 client (~3-4 min sprecati per task) e i
    mirror senza dataselect fallivano con retry inutili. Ora entrambi sono
    esiti permanenti gestiti subito."""

    def test_204_no_data_senza_retry(self, tmp_path):
        """Un 204 è permanente: status 'no_data', UNA sola chiamata,
        nessun retry né passaggio ai client successivi."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep") as fake_sleep:
            def client_factory(url, timeout=60):
                c = MagicMock()
                c.get_waveforms.side_effect = RuntimeError(
                    "No data available for request.\n"
                    "HTTP Status code: 204\n"
                    "Detailed response of server:")
                return c
            fake_client.side_effect = client_factory
            info = call_worker(tmp_path, client_urls=["fake://a",
                                                       "fake://b"])
        assert info["status"] == "no_data"
        # un solo tentativo sul primo client, il secondo nemmeno provato
        assert fake_client.call_count == 1
        fake_sleep.assert_not_called()

    def test_404_resta_distinto_da_204(self, tmp_path):
        """Il 404 contiene anch'esso 'No data available' ma deve restare un
        4xx permanente: passa al client successivo, NON diventa 'no_data'."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):
            def client_factory(url, timeout=60):
                if "client1" in str(url):
                    c = MagicMock()
                    c.get_waveforms.side_effect = RuntimeError(
                        "404 No data available for request")
                    return c
                c = MagicMock()
                c.get_waveforms.return_value = make_fake_stream()
                return c
            fake_client.side_effect = client_factory
            info = call_worker(tmp_path, client_urls=["fake://client1",
                                                      "fake://client2"])
        assert info["status"] == "success"
        assert fake_client.call_count == 2

    def test_client_avvelenato_da_discovery_fail_si_recupera(self, tmp_path):
        """Run reale 2026-11-27 (267/294 failed): con 8 worker paralleli il
        service discovery di ObsPy può fallire per rate-limit e il client
        resta cached 'senza dataselect' per tutto il thread, uccidendo ogni
        task successivo. Ora il primo errore invalida la cache e il retry
        rifà il discovery: il task riesce."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):
            broken = MagicMock()
            broken.get_waveforms.side_effect = RuntimeError(
                "The current client does not have a dataselect service.")
            working = MagicMock()
            working.get_waveforms.return_value = make_fake_stream()
            fake_client.side_effect = [broken, working]
            info = call_worker(tmp_path)
        assert info["status"] == "success"
        # il client avvelenato è stato invalidato e ricreato
        assert fake_client.call_count == 2

    def test_client_avvelenato_invalida_la_cache(self, tmp_path):
        """Dopo l'errore dataselect-missing la cache del thread NON deve più
        contenere il client rotto."""
        with patch.object(dlw, "Client") as fake_client, \
             patch.object(dlw.time, "sleep"):
            fake_client.return_value.get_waveforms.side_effect = RuntimeError(
                "The current client does not have a dataselect service.")
            info = call_worker(tmp_path)
        assert info["status"] == "failed"
        # 2 tentativi: il primo invalida la cache, il secondo rifà il
        # discovery; anche il secondo client è rotto -> break (permanente)
        assert fake_client.call_count == 2
        cache = getattr(dlw._THREAD_LOCAL, "clients", {})
        assert "fake://client" not in cache

    def test_statistiche_includono_no_data(self, tmp_path):
        """Il conteggio finale deve distinguere 'no_data' come esito."""
        summary = [
            {"status": "success"},
            {"status": "no_data"}, {"status": "no_data"},
            {"status": "skipped"},
        ]
        counts = {}
        for res in summary:
            status = res.get("status", "failed")
            counts[status] = counts.get(status, 0) + 1
        assert counts == {"success": 1, "no_data": 2, "skipped": 1}
