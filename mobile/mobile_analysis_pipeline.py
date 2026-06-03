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

# Import PROJECT_ROOT for consistent path resolution
from path_utils import PROJECT_ROOT

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Aggiungi path per import mobile
sys.path.insert(0, str(PROJECT_ROOT / "mobile"))


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
    
    return parser.parse_args()


def load_config(config_path: Optional[Path] = None) -> dict:
    """Carica configurazione da file YAML."""
    if config_path is None:
        # Prova a caricare config default
        default_config = PROJECT_ROOT / "mobile" / "config" / "alert_config.yaml"
        if default_config.exists():
            config_path = default_config
    
    if config_path is None or not config_path.exists():
        return {}
    
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        logger.info(f"Configurazione caricata da {config_path}")
        return config or {}
    except Exception as e:
        logger.warning(f"Errore caricamento config: {e}")
        return {}


def run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    dry_run: bool = False,
    check: bool = True
) -> bool:
    """Esegue un comando."""
    if dry_run:
        logger.info(f"[DRY RUN] {' '.join(cmd)}")
        return True
    
    logger.info(f"Esecuzione: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True
        )
        
        if result.stdout:
            logger.debug(f"Output: {result.stdout}")
        if result.stderr:
            logger.warning(f"Error: {result.stderr}")
        
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Comando fallito: {' '.join(cmd)}")
        logger.error(f"Exit code: {e.returncode}")
        logger.error(f"Stderr: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Errore inaspettato: {e}")
        return False


def validate_input_files(input_csv: Path, stations_csv: Path) -> bool:
    """Valida che i file di input esistano."""
    errors = []
    
    if not input_csv.exists():
        errors.append(f"File non trovato: {input_csv}")
    
    if not stations_csv.exists():
        errors.append(f"File non trovato: {stations_csv}")
    
    if errors:
        for error in errors:
            logger.error(error)
        return False
    
    return True


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
    import shutil
    
    try:
        shutil.copy(input_csv, output_dir / input_csv.name)
        shutil.copy(stations_csv, output_dir / stations_csv.name)
        logger.info(f"File di input copiati in {output_dir}")
    except Exception as e:
        logger.warning(f"Errore copia file: {e}")


def run_mobile_pipeline():
    """Esecuzione principale della pipeline mobile."""
    args = parse_arguments()
    
    logger.info("=" * 70)
    logger.info("PIPELINE ANALISI MOBILE + ALLARMI SISMICI")
    logger.info("=" * 70)
    
    # Tempo di inizio
    start_time = time.time()
    
    try:
        # 1. Validazione input
        if not validate_input_files(args.input_csv, args.stations_csv):
            logger.error("Validazione input fallita")
            sys.exit(1)
        
        # 2. Carica configurazione
        config = load_config(args.config)
        
        # 3. Crea struttura output
        create_output_structure(args.output_dir)
        
        # 4. Copia file di input
        copy_input_files(args.input_csv, args.stations_csv, args.output_dir)
        
        # 5. Determina script directory
        script_dir = PROJECT_ROOT / "examples" / "mobile_devices"
        if not script_dir.exists():
            logger.error(f"Directory script non trovata: {script_dir}")
            sys.exit(1)
        
        # 6. Esecuzione script in sequenza
        python_exe = sys.executable
        
        # 6.1. process_pipeline.py - Georeferenziazione
        logger.info("
FASE 1: Georeferenziazione e pulizia dati...")
        cmd1 = [
            python_exe,
            str(script_dir / "process_pipeline.py"),
            "--input-csv", str(args.input_csv),
            "--stations-csv", str(args.stations_csv),
            "--output-dir", str(args.output_dir / "interim")
        ]
        if not run_command(cmd1, cwd=str(script_dir)):
            logger.error("process_pipeline.py fallito")
            sys.exit(1)
        
        # 6.2. associa_eventi.py - Clustering eventi
        logger.info("
FASE 2: Clustering eventi e creazione catalogo...")
        cmd2 = [
            python_exe,
            str(script_dir / "associa_eventi.py")
        ]
        if not run_command(cmd2, cwd=str(script_dir)):
            logger.error("associa_eventi.py fallito")
            sys.exit(1)
        
        # 6.3. prepara_ml.py - Feature engineering
        logger.info("
FASE 3: Feature engineering per ML...")
        cmd3 = [
            python_exe,
            str(script_dir / "prepara_ml.py")
        ]
        if not run_command(cmd3, cwd=str(script_dir)):
            logger.error("prepara_ml.py fallito")
            sys.exit(1)
        
        # 6.4. train_modello.py - Training e allarmi
        logger.info("
FASE 4: Training modello e generazione allarmi...")
        cmd4 = [
            python_exe,
            str(script_dir / "train_modello.py")
        ]
        
        # Aggiungi parametri opzionali
        if args.generate_alerts:
            cmd4.append("--generate-alerts")
        if args.model_type:
            cmd4.extend(["--model-type", args.model_type])
        
        if not run_command(cmd4, cwd=str(script_dir)):
            logger.error("train_modello.py fallito")
            sys.exit(1)
        
        # 7. Copia output in directory finale
        logger.info("
Copia output in directory finale...")
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
                import shutil
                shutil.copy(src, dst)
                logger.info(f"   Copiato: {file}")
        
        # 8. Copia modelli e allarmi
        logger.info("
Copia modelli e log allarmi...")
        model_dir = PROJECT_ROOT / "mobile" / "models"
        alerts_dir = PROJECT_ROOT / "mobile" / "alerts"
        
        if model_dir.exists():
            for model_file in model_dir.glob("*"):
                import shutil
                shutil.copy(model_file, args.output_dir / "models" / model_file.name)
                logger.info(f"   Modello: {model_file.name}")
        
        if alerts_dir.exists():
            for alert_file in alerts_dir.glob("*"):
                import shutil
                shutil.copy(alert_file, args.output_dir / "alerts" / alert_file.name)
                logger.info(f"   Allarmi: {alert_file.name}")
        
        # Tempo totale
        elapsed_time = time.time() - start_time
        logger.info("
" + "=" * 70)
        logger.info("PIPELINE COMPLETATA CON SUCCESSO!")
        logger.info("=" * 70)
        logger.info(f"Output salvato in: {args.output_dir}")
        logger.info(f"Tempo totale: {elapsed_time:.2f} secondi")
        logger.info("=" * 70)
        
        return True
        
    except KeyboardInterrupt:
        logger.warning("Pipeline interrotta dall'utente")
        return False
    except Exception as e:
        logger.error(f"Errore critico: {str(e)}", exc_info=True)
        return False


if __name__ == "__main__":
    success = run_mobile_pipeline()
    sys.exit(0 if success else 1)