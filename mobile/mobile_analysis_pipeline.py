#!/usr/bin/env python3
"""
Pipeline unificata per analisi mobile e generazione allarmi sismici.

Questo script orchestra l'esecuzione sequenziale degli script di analisi mobile:
1. process_pipeline.py - Georeferenziazione e pulizia dati
2. associa_eventi.py - Clustering eventi e creazione catalogo
3. prepara_ml.py - Feature engineering per ML
4. train_modello.py - Training modello e generazione allarmi

Usage:
    python mobile_analysis_pipeline.py \
        --input-csv scoperte_automatiche.csv.gz \
        --stations-csv stations.csv \
        --output-dir runs/mobile_analysis \
        --min-stations 18 \
        --alert-threshold 0.7
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List
import logging
import yaml
import shutil

# Import PROJECT_ROOT for consistent path resolution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from path_utils import PROJECT_ROOT

# Import data validation functions
sys.path.insert(0, str(PROJECT_ROOT / "mobile"))
from data_validator import (
    validate_csv_file,
    validate_stations,
    DataValidationError
)

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default timeout for subprocess commands (in seconds)
DEFAULT_TIMEOUT = 600  # 10 minutes for ML operations


def parse_arguments():
    """Parsa gli argomenti da riga di comando."""
    parser = argparse.ArgumentParser(
        description="Pipeline unificata per analisi mobile e generazione allarmi"
    )
    
    # Input/Output
    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help="File CSV input (es: scoperte_automatiche.csv.gz)"
    )
    parser.add_argument(
        "--stations-csv",
        type=Path,
        required=True,
        help="File CSV stazioni (es: stations.csv)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default="runs/mobile_analysis",
        help="Cartella di output (default: runs/mobile_analysis)"
    )
    
    # Parametri analisi
    parser.add_argument(
        "--min-stations",
        type=int,
        default=18,
        help="Soglia stazioni per allarme (default: 18)"
    )
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=0.7,
        help="Soglia indice di rischio per allarme (default: 0.7)"
    )
    parser.add_argument(
        "--target-window",
        type=int,
        default=24,
        help="Finestra temporale per target (ore, default: 24)"
    )
    
    # Opzioni esecuzione
    parser.add_argument(
        "--generate-alerts",
        action="store_true",
        help="Genera allarmi attivi durante il training"
    )
    parser.add_argument(
        "--model-type",
        choices=["xgboost", "random_forest"],
        default="xgboost",
        help="Tipo di modello ML (default: xgboost)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="File di configurazione YAML (opzionale)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra cosa verrebbe eseguito senza eseguire"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Numero di processi paralleli (default: 1)"
    )
    
    # Robustezza: timeout per comandi
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout per comandi in secondi (default: {DEFAULT_TIMEOUT})"
    )
    
    # Robustezza: cleanup automatico in caso di errore
    parser.add_argument(
        "--cleanup-on-error",
        action="store_true",
        help="Pulisce la directory di output in caso di errore"
    )
    
    return parser.parse_args()


def load_config(config_path: Optional[Path] = None) -> dict:
    """Carica configurazione da file YAML."""
    if config_path is None:
        # Prova a caricare config default
        default_config = PROJECT_ROOT / "mobile" / "config" / "alert_config.yaml"
        if default_config.exists():
            config_path = default_config
    
    if config_path is None or not config_path.exists():
        logger.warning("Nessun file di configurazione trovato, uso valori default")
        return {}
    
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        logger.info(f"Configurazione caricata da {config_path}")
        return config or {}
    except yaml.YAMLError as e:
        logger.error(f"Errore parsing configurazione YAML: {e}")
        return {}
    except Exception as e:
        logger.error(f"Errore caricamento config: {e}")
        return {}


def run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    dry_run: bool = False,
    check: bool = True,
    timeout: Optional[int] = None
) -> bool:
    """
    Esegue un comando con timeout e gestione errori migliorata.
    
    Args:
        cmd: Lista di argomenti del comando
        cwd: Working directory
        dry_run: Se True, solo mostra il comando senza eseguire
        check: Se True, solleva eccezione se il comando fallisce
        timeout: Timeout in secondi
    
    Returns:
        True se comando completato con successo
    
    Raises:
        subprocess.TimeoutExpired: Se timeout superato
        subprocess.CalledProcessError: Se comando fallisce
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    
    if dry_run:
        logger.info(f"[DRY RUN] {' '.join(cmd)}")
        return True
    
    logger.info(f"Esecuzione: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            timeout=timeout,
            capture_output=True,
            text=True
        )
        
        if result.stdout:
            logger.debug(f"Output: {result.stdout[:500]}")
        if result.stderr:
            logger.warning(f"Stderr: {result.stderr[:500]}")
        
        return True
    except subprocess.TimeoutExpired as exc:
        logger.error(f"Comando timeout dopo {timeout}s: {' '.join(cmd)}")
        raise
    except subprocess.CalledProcessError as exc:
        logger.error(f"Comando fallito: {' '.join(cmd)}")
        logger.error(f"Exit code: {exc.returncode}")
        if exc.stderr:
            logger.error(f"Stderr: {exc.stderr[:500]}")
        raise
    except Exception as exc:
        logger.error(f"Errore inaspettato in run_command: {exc}")
        raise


def validate_input_files(input_csv: Path, stations_csv: Path) -> bool:
    """
    Valida che i file di input esistano e abbiano il formato corretto.
    
    Args:
        input_csv: Path al file CSV input
        stations_csv: Path al file CSV stazioni
    
    Returns:
        True se validazione passa
    
    Raises:
        DataValidationError: Se validazione fallisce
        FileNotFoundError: Se file non esiste
    """
    errors = []
    
    # Check files exist
    if not input_csv.exists():
        errors.append(f"File non trovato: {input_csv}")
    
    if not stations_csv.exists():
        errors.append(f"File non trovato: {stations_csv}")
    
    if errors:
        for error in errors:
            logger.error(error)
        return False
    
    # Validate CSV files
    try:
        # Input CSV should have basic columns
        input_df = validate_csv_file(
            input_csv,
            required_columns={"station"}
        )
        logger.info(f"Input CSV validato: {input_csv.name} ({len(input_df)} righe)")
        
        # Stations CSV should have coordinate columns
        stations_df = validate_csv_file(
            stations_csv,
            required_columns={"station", "latitude", "longitude"}
        )
        
        # Validate geographic coordinates
        is_valid, msg = validate_stations(stations_df)
        if not is_valid:
            raise DataValidationError(f"Coordinate geografiche non valide: {msg}")
            
        logger.info(f"Stations CSV validato: {stations_csv.name} ({len(stations_df)} stazioni)")
        
        return True
        
    except DataValidationError as e:
        logger.error(f"Validazione CSV fallita: {e.message}")
        for err in e.errors:
            logger.error(f"  - {err}")
        return False
    except Exception as e:
        logger.error(f"Errore validazione input: {e}")
        return False


def create_output_structure(output_dir: Path) -> None:
    """Crea la struttura di directory di output."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Sottodirectory
    subdirs = [
        "interim",
        "processed", 
        "output",
        "models",
        "alerts",
        "logs"
    ]
    
    for subdir in subdirs:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Struttura output creata: {output_dir}")


def copy_input_files(input_csv: Path, stations_csv: Path, output_dir: Path) -> None:
    """Copia i file di input nella directory di output."""
    try:
        shutil.copy(input_csv, output_dir / input_csv.name)
        shutil.copy(stations_csv, output_dir / stations_csv.name)
        logger.info(f"File di input copiati in {output_dir}")
    except Exception as e:
        logger.warning(f"Errore copia file: {e}")
        raise


def cleanup_output_directory(output_dir: Path) -> None:
    """Pulisce la directory di output in caso di errore."""
    try:
        if output_dir.exists():
            logger.info(f"Pulizia directory: {output_dir}")
            shutil.rmtree(output_dir, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Errore durante pulizia: {e}")


def run_mobile_pipeline():
    """Esecuzione principale della pipeline mobile."""
    args = parse_arguments()
    
    if args.dry_run:
        logger.info("Dry run completata.")
        return True
    
    logger.info("=" * 70)
    logger.info("PIPELINE ANALISI MOBILE + ALLARMI SISMICI")
    logger.info("=" * 70)
    
    # Tempo di inizio
    start_time = time.time()
    
    # Track created files for cleanup
    created_files = []
    
    try:
        # 1. Validazione input
        if not validate_input_files(args.input_csv, args.stations_csv):
            logger.error("Validazione input fallita")
            if args.cleanup_on_error:
                cleanup_output_directory(args.output_dir)
            sys.exit(1)
        
        # 2. Carica configurazione
        config = load_config(args.config)
        
        # 3. Crea struttura output
        create_output_structure(args.output_dir)
        created_files.append(args.output_dir)
        
        # 4. Copia file di input
        copy_input_files(args.input_csv, args.stations_csv, args.output_dir)
        
        # 5. Determina script directory
        script_dir = PROJECT_ROOT / "examples" / "mobile_devices"
        if not script_dir.exists():
            logger.error(f"Directory script non trovata: {script_dir}")
            if args.cleanup_on_error:
                cleanup_output_directory(args.output_dir)
            sys.exit(1)
        
        # 6. Esecuzione script in sequenza
        python_exe = sys.executable
        
        # 6.1. process_pipeline.py - Georeferenziazione
        logger.info("\nFASE 1: Georeferenziazione e pulizia dati...")
        cmd1 = [
            python_exe,
            str(script_dir / "process_pipeline.py"),
            "--input-csv", str(args.input_csv),
            "--stations-csv", str(args.stations_csv),
            "--output-dir", str(args.output_dir / "interim")
        ]
        if not run_command(cmd1, cwd=str(script_dir), timeout=args.timeout):
            logger.error("process_pipeline.py fallito")
            if args.cleanup_on_error:
                cleanup_output_directory(args.output_dir)
            sys.exit(1)
        
        # 6.2. associa_eventi.py - Clustering eventi
        logger.info("\nFASE 2: Clustering eventi e creazione catalogo...")
        cmd2 = [
            python_exe,
            str(script_dir / "associa_eventi.py")
        ]
        if not run_command(cmd2, cwd=str(script_dir), timeout=args.timeout):
            logger.error("associa_eventi.py fallito")
            if args.cleanup_on_error:
                cleanup_output_directory(args.output_dir)
            sys.exit(1)
        
        # 6.3. prepara_ml.py - Feature engineering
        logger.info("\nFASE 3: Feature engineering per ML...")
        cmd3 = [
            python_exe,
            str(script_dir / "prepara_ml.py")
        ]
        if not run_command(cmd3, cwd=str(script_dir), timeout=args.timeout * 2):
            logger.error("prepara_ml.py fallito")
            if args.cleanup_on_error:
                cleanup_output_directory(args.output_dir)
            sys.exit(1)
        
        # 6.4. train_modello.py - Training e allarmi
        logger.info("\nFASE 4: Training modello e generazione allarmi...")
        cmd4 = [
            python_exe,
            str(script_dir / "train_modello.py")
        ]
        
        # Aggiungi parametri opzionali
        if args.generate_alerts:
            cmd4.append("--generate-alerts")
        if args.model_type:
            cmd4.extend(["--model-type", args.model_type])
        
        if not run_command(cmd4, cwd=str(script_dir), timeout=args.timeout * 3):
            logger.error("train_modello.py fallito")
            if args.cleanup_on_error:
                cleanup_output_directory(args.output_dir)
            sys.exit(1)
        
        # 7. Copia output in directory finale
        logger.info("\nCopia output in directory finale...")
        output_files = [
            "catalogo_terremoti_unici.csv",
            "dataset_ml_sismico.csv",
            "output_eventi_georeferenziati.csv.gz",
            "output_eventi_qgis.geojson"
        ]
        
        for file in output_files:
            src = script_dir / file
            dst = args.output_dir / "output" / file
            if src.exists():
                shutil.copy(src, dst)
                logger.info(f"   Copiato: {file}")
        
        # 8. Copia modelli e allarmi
        logger.info("\nCopia modelli e log allarmi...")
        model_dir = PROJECT_ROOT / "mobile" / "models"
        alerts_dir = PROJECT_ROOT / "mobile" / "alerts"
        
        if model_dir.exists():
            for model_file in model_dir.glob("*"):
                shutil.copy(model_file, args.output_dir / "models" / model_file.name)
                logger.info(f"   Modello: {model_file.name}")
        
        if alerts_dir.exists():
            for alert_file in alerts_dir.glob("*"):
                shutil.copy(alert_file, args.output_dir / "alerts" / alert_file.name)
                logger.info(f"   Allarmi: {alert_file.name}")
        
        # Tempo totale
        elapsed_time = time.time() - start_time
        logger.info("\n" + "=" * 70)
        logger.info("PIPELINE COMPLETATA CON SUCCESSO!")
        logger.info("=" * 70)
        logger.info(f"Output salvato in: {args.output_dir}")
        logger.info(f"Tempo totale: {elapsed_time:.2f} secondi")
        logger.info("=" * 70)
        
        return True
        
    except subprocess.TimeoutExpired as exc:
        logger.error(f"❌ Timeout superato: {exc}")
        if args.cleanup_on_error:
            cleanup_output_directory(args.output_dir)
        return False
    except DataValidationError as exc:
        logger.error(f"❌ Validazione dati fallita: {exc.message}")
        for err in exc.errors:
            logger.error(f"   {err}")
        if args.cleanup_on_error:
            cleanup_output_directory(args.output_dir)
        return False
    except FileNotFoundError as exc:
        logger.error(f"❌ File non trovato: {exc}")
        if args.cleanup_on_error:
            cleanup_output_directory(args.output_dir)
        return False
    except subprocess.CalledProcessError as exc:
        logger.error(f"❌ Comando fallito con codice {exc.returncode}")
        if args.cleanup_on_error:
            cleanup_output_directory(args.output_dir)
        return False
    except KeyboardInterrupt:
        logger.warning("Pipeline interrotta dall'utente")
        if args.cleanup_on_error:
            cleanup_output_directory(args.output_dir)
        return False
    except Exception as e:
        logger.error(f"❌ Errore critico: {str(e)}", exc_info=True)
        if args.cleanup_on_error:
            cleanup_output_directory(args.output_dir)
        return False


if __name__ == "__main__":
    success = run_mobile_pipeline()
    sys.exit(0 if success else 1)
