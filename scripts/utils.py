from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import yaml
import pandas as pd
from pathlib import Path

def get_project_root() -> Path:
    """Restituisce il percorso root del progetto (due livelli sopra utils.py)."""
    return Path(__file__).resolve().parents[1]


def setup_logger(name: str) -> logging.Logger:
    """
    Configura e restituisce un logger per la pipeline.
    L'output viene salvato su file (logs/pipeline.log) e stampato a video con rotazione.
    """
    log_file = get_project_root() / "logs" / "pipeline.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    # Evita di duplicare gli handler se il logger è già stato inizializzato
    if not logger.hasHandlers():
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        fh = RotatingFileHandler(log_file, mode="a", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    return logger


# Inizializza il logger per l'uso interno a utils.py
logger = setup_logger("utils")


def load_config(config_path: str = "config.yaml") -> dict:
    """Carica e restituisce il file di configurazione globale come dizionario."""
    path = get_project_root() / config_path
    if not path.exists():
        logger.warning(f"File di configurazione {path} non trovato. Verranno usati i default hardcoded.")
        return {}
    
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_csv_with_checks(path: Path, required_columns: set[str]) -> pd.DataFrame:
    """
    Carica un file CSV e verifica la presenza delle colonne richieste.
    Interrompe l'esecuzione segnalando esattamente quali colonne mancano in caso di errore.
    """
    df = pd.read_csv(path)
    missing = required_columns - set(df.columns)
    if missing:
        err_msg = f"Errore: il file {path} è privo delle colonne richieste: {', '.join(sorted(missing))}"
        logger.error(err_msg)
        raise ValueError(err_msg)
    return df