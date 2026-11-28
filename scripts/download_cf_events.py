#!/usr/bin/env python
"""
Scarica gli eventi sismici del catalogo INGV (servizio fdsnws/event) per la
zona dei Campi Flegrei. Il catalogo è il ground truth del Target_Allarme:
"ora con almeno un evento di magnitudine >= M entro H ore" — sostituisce il
vecchio proxy max(delta_seconds) > 15.0, degenere sui dati reali
(banda 7.96-9.88 s senza segnale discriminante, misurata il 2026-11-28).

Il servizio FDSN è inaffidabile per esperienza consolidata (rate-limit,
503 transitori): download con retry backoff (5/15/45s, 4 tentativi) come
il downloader waveform. Il formato text usa '|' come separatore.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils import load_config, setup_logger  # noqa: E402

logger = setup_logger("download_cf_events")

DEFAULT_EVENT_URL = "https://webservices.ingv.it/fdsnws/event/1/query"
RETRY_DELAYS = (5, 15, 45)

# Colonne del formato text FDSN event (separatore '|') mappate su nomi interni
COLUMN_MAP = {
    "#EventID": "event_id",
    "Time": "time",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Depth/Km": "depth_km",
    "MagType": "mag_type",
    "Magnitude": "magnitude",
    "EventLocationName": "event_location_name",
    "EventType": "event_type",
}


def build_query_url(base_url: str, start: str, end: str,
                    lat: float, lon: float, radius_km: float) -> str:
    """Costruisce l'URL della query fdsnws/event con filtro geografico."""
    return (
        f"{base_url}?starttime={start}&endtime={end}"
        f"&latitude={lat}&longitude={lon}&maxradius={radius_km}"
        f"&format=text"
    )


def fetch_events_text(url: str, timeout: float = 60.0) -> str:
    """Scarica il catalogo in formato text con retry backoff.

    I 4xx sono fallimenti permanenti (nessun retry), i 5xx/timeout/transitori
    vengono ritentati con pause 5/15/45s.
    """
    delays = (0,) + RETRY_DELAYS
    last_exc: Exception | None = None
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            logger.info(f"Attesa {delay}s prima del retry {attempt}...")
            time.sleep(delay)
        try:
            logger.info(f"Richiesta catalogo eventi: {url} (tentativo {attempt})")
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            logger.warning(f"Tentativo {attempt} fallito: HTTP {exc.code} {exc.reason}")
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                # 4xx permanente (404 area senza eventi inclusa): nessun retry
                raise
        except Exception as exc:  # timeout, connessione, DNS: transitori
            last_exc = exc
            logger.warning(f"Tentativo {attempt} fallito: {exc}")
    raise RuntimeError(f"Catalogo eventi non scaricabile dopo {len(delays)} tentativi: {last_exc}")


def parse_events_text(raw_text: str):
    """Parsing del formato text FDSN in un DataFrame pandas."""
    import pandas as pd

    if not raw_text.strip():
        raise ValueError("Risposta vuota dal servizio eventi")
    df = pd.read_csv(io.StringIO(raw_text), sep="|")
    df = df.rename(columns=COLUMN_MAP)
    missing = set(COLUMN_MAP.values()) - set(df.columns)
    if missing:
        raise ValueError(
            f"Formato catalogo inatteso: mancano le colonne {sorted(missing)}"
        )
    df["time"] = df["time"].str.strip()
    # Gli spazi attorno ai valori sono frequenti nel formato text
    for col in ("magnitude", "latitude", "longitude", "depth_km"):
        df[col] = df[col].astype(float)
    keep = list(COLUMN_MAP.values())
    return df[keep]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scarica gli eventi del catalogo INGV per il target di training."
    )
    parser.add_argument("--start", required=True, help="Data/ora inizio (ISO)")
    parser.add_argument("--end", required=True, help="Data/ora fine (ISO)")
    parser.add_argument(
        "--output-csv", type=Path, required=True,
        help="CSV di destinazione (le directory padre vengono create).",
    )
    parser.add_argument("--url", default=DEFAULT_EVENT_URL)
    parser.add_argument("--lat", type=float, default=None,
                        help="Latitudine centro (default da config.yaml: catalog)")
    parser.add_argument("--lon", type=float, default=None)
    parser.add_argument("--radius-km", type=float, default=None)
    args = parser.parse_args()

    catalog_cfg = load_config().get("catalog", {}) or {}
    lat = args.lat if args.lat is not None else catalog_cfg.get("center_lat", 40.82)
    lon = args.lon if args.lon is not None else catalog_cfg.get("center_lon", 14.14)
    radius = args.radius_km if args.radius_km is not None else catalog_cfg.get("radius_km", 25)

    url = build_query_url(args.url, args.start, args.end, lat, lon, radius)
    raw = fetch_events_text(url)
    df = parse_events_text(raw)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output_csv, index=False)
    logger.info(
        f"Salvati {len(df)} eventi in {args.output_csv} "
        f"(magnitudini: min={df['magnitude'].min()}, max={df['magnitude'].max()})"
    )


if __name__ == "__main__":
    main()
