#!/usr/bin/env python3
"""
Monitoraggio Real-Time Campi Flegrei - Issue #4

Script per monitoraggio continuo dell'area Campi Flegrei con:
- Download automatico dati FDSN (INGV)
- Predizione rischio con modelli ML
- Generazione allarmi tramite AlertSystem
- Esecuzione come servizio/daemon

Usage:
    # Esecuzione manuale (test)
    python mobile/monitor_campi_flegrei.py --dry-run
    
    # Esecuzione continua (default: ogni 10 minuti)
    python mobile/monitor_campi_flegrei.py
    
    # Esecuzione con configurazione custom
    python mobile/monitor_campi_flegrei.py --config mobile/config/monitor_config.yaml
    
    # Esecuzione come daemon (Linux)
    nohup python mobile/monitor_campi_flegrei.py --daemon > monitor.log 2>&1 &

Environment Variables:
    MONITOR_INTERVAL: Intervallo in minuti (default: 10)
    MONITOR_MIN_STATIONS: Minimo stazioni richieste (default: 18)
    MONITOR_RISK_THRESHOLD: Soglia rischio (default: 0.7)
    ENVIRONMENT: Ambiente (dev, prod, test)
    MODEL_VERSION: Versione modello da usare (default: current)
"""

import argparse
import os
import sys
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import logging

import pandas as pd
import numpy as np

from path_utils import PROJECT_ROOT

from mobile.alert_system import AlertSystem, AlertConfig, AlertMessage
from mobile.logging_config import setup_monitoring_logger
from mobile.model_versioning import ModelVersionManager, get_model_manager


# Constants
DEFAULT_INTERVAL_MINUTES = 10
DEFAULT_MIN_STATIONS = 18
DEFAULT_RISK_THRESHOLD = 0.7
DEFAULT_AREA = {"latitude": 40.8062, "longitude": 14.1410, "radius_km": 20.0}

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(sig, frame):
    """Handle shutdown signals."""
    global shutdown_requested
    shutdown_requested = True
    logging.info(f"Received signal {sig}, shutting down gracefully...")


class CampiFlegreiMonitor:
    """Monitor per l'area Campi Flegrei."""
    
    def __init__(
        self,
        config_path: Optional[Path] = None,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        min_stations: int = DEFAULT_MIN_STATIONS,
        risk_threshold: float = DEFAULT_RISK_THRESHOLD,
        dry_run: bool = False,
        daemon: bool = False,
        model_version: Optional[str] = None
    ):
        self.config_path = config_path or PROJECT_ROOT / "mobile/config/monitor_config.yaml"
        self.interval_seconds = interval_minutes * 60
        self.min_stations = min_stations
        self.risk_threshold = risk_threshold
        self.dry_run = dry_run
        self.daemon = daemon
        self.model_version = model_version or os.getenv("MODEL_VERSION", "current")
        
        # Setup logging
        self.logger = setup_monitoring_logger("campi_flegrei_monitor")
        
        # Initialize components
        self.alert_system: Optional[AlertSystem] = None
        self.model: Optional[Any] = None
        self.model_metadata: Dict[str, Any] = {}
        
        self._initialize_alert_system()
        self._load_model()
        
        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        self.logger.info("Campi Flegrei Monitor initialized")
        self.logger.info(f"Model version: {self.model_version}")
    
    def _initialize_alert_system(self) -> None:
        """Inizializza il sistema di alert."""
        try:
            env = os.getenv("ENVIRONMENT", "prod")
            config_path = PROJECT_ROOT / "mobile/config" / f"alert_config.{env}.yaml"
            
            if config_path.exists():
                config = AlertConfig.from_yaml(str(config_path))
            else:
                config = AlertConfig.from_yaml(str(PROJECT_ROOT / "mobile/config/alert_config.yaml"))
            
            self.alert_system = AlertSystem(config)
            self.logger.info(f"Alert system initialized with {env} config")
        except Exception as e:
            self.logger.error(f"Failed to initialize alert system: {e}")
            self.alert_system = None
    
    def _load_model(self) -> None:
        """Carica il modello ML con versioning."""
        try:
            # Try to load with versioning system
            manager = get_model_manager("xgboost")
            self.model, self.model_metadata = manager.load_model(self.model_version)
            self.logger.info(f"Model loaded: {self.model_version}")
            self.logger.info(f"Model metadata: {self.model_metadata}")
        except Exception as e:
            self.logger.warning(f"Could not load model with versioning: {e}")
            self.logger.warning("Will use fallback risk calculation")
            self.model = None
            self.model_metadata = {}
    
    def _get_fdsn_client(self):
        """Crea client FDSN per INGV."""
        from obspy.clients.fdsn import Client
        return Client("INGV")
    
    def _get_stations_in_area(self) -> list:
        """Ottieni lista stazioni nell'area Campi Flegrei."""
        from obspy import UTCDateTime
        
        client = self._get_fdsn_client()
        lat = DEFAULT_AREA["latitude"]
        lon = DEFAULT_AREA["longitude"]
        radius = DEFAULT_AREA["radius_km"]
        
        try:
            start_time = UTCDateTime.now() - timedelta(days=1)
            end_time = UTCDateTime.now()
            
            inventory = client.get_stations(
                starttime=start_time,
                endtime=end_time,
                latitude=lat,
                longitude=lon,
                minradius=radius,
                maxradius=radius,
                network="IV",
                level="station"
            )
            
            stations = []
            for network in inventory:
                for station in network:
                    stations.append(station.code)
            
            self.logger.info(f"Found {len(stations)} stations in area")
            return stations
        
        except Exception as e:
            self.logger.error(f"Error getting stations: {e}")
            return [
                "CAAM", "CAFL", "CAWE", "CBAC", "CBAG", "CCAP", "CFMN", "CMIS",
                "CMSN", "CMTS", "CNIS", "COLB", "CPIS", "CPOZ", "CQUE", "CSFT",
                "CSOB", "CSTH", "CUMA", "IBCM", "IBRN", "IOCA", "IPSM", "PTMR"
            ]
    
    def _fetch_latest_waveforms(self, stations: list, minutes: int = 15) -> Optional[pd.DataFrame]:
        """Scarica gli ultimi waveform per le stazioni specificate."""
        from obspy import UTCDateTime
        from obspy.clients.fdsn import Client
        
        client = Client("INGV")
        end_time = UTCDateTime.now()
        start_time = end_time - (minutes * 60)
        
        all_data = []
        
        try:
            for station in stations[:self.min_stations]:
                try:
                    st = client.get_waveforms(
                        network="IV",
                        station=station,
                        location="*",
                        channel="HHZ",
                        starttime=start_time,
                        endtime=end_time,
                        attach_response=True
                    )
                    
                    for tr in st:
                        data = {
                            "station": tr.stats.station,
                            "network": tr.stats.network,
                            "channel": tr.stats.channel,
                            "starttime": tr.stats.starttime.isoformat(),
                            "endtime": tr.stats.endtime.isoformat(),
                            "sampling_rate": tr.stats.sampling_rate,
                            "npts": tr.stats.npts,
                            "mean": float(np.mean(tr.data)),
                            "std": float(np.std(tr.data)),
                            "min": float(np.min(tr.data)),
                            "max": float(np.max(tr.data))
                        }
                        all_data.append(data)
                        
                except Exception as e:
                    self.logger.warning(f"Error fetching data for station {station}: {e}")
                    continue
            
            if not all_data:
                self.logger.warning("No waveform data retrieved")
                return None
            
            df = pd.DataFrame(all_data)
            self.logger.info(f"Retrieved {len(df)} waveform records")
            return df
            
        except Exception as e:
            self.logger.error(f"Error fetching waveforms: {e}")
            return None
    
    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pre-elabora i dati per la predizione."""
        df["starttime"] = pd.to_datetime(df["starttime"])
        df["hour"] = df["starttime"].dt.hour
        df["minute"] = df["starttime"].dt.minute
        df["amplitude_range"] = df["max"] - df["min"]
        df["signal_to_noise"] = df["std"] / (df["amplitude_range"] + 1e-10)
        return df
    
    def _predict_risk(self, df: pd.DataFrame) -> float:
        """Predice il livello di rischio usando il modello ML."""
        if self.model is not None:
            try:
                features = df[["mean", "std", "min", "max", "amplitude_range", "hour", "minute"]]
                risk_scores = self.model.predict(features)
                risk = float(np.mean(risk_scores))
                self.logger.info(f"Predicted risk score (model {self.model_version}): {risk:.4f}")
                return risk
            except Exception as e:
                self.logger.error(f"Error using ML model: {e}")
        
        # Fallback: simple threshold-based logic
        self.logger.warning("Using fallback risk calculation (no ML model)")
        std_normalized = df["std"].mean() / 1e6
        amplitude_normalized = df["amplitude_range"].mean() / 1e6
        risk = (std_normalized * 0.6) + (amplitude_normalized * 0.4)
        self.logger.info(f"Predicted risk score (fallback): {risk:.4f}")
        return float(risk)
    
    def _check_thresholds(self, risk_score: float, stations_count: int) -> tuple[bool, str]:
        """Verifica se le soglie sono superate."""
        alerts = []
        
        if stations_count < self.min_stations:
            alerts.append(f"Stazioni insufficienti: {stations_count}/{self.min_stations}")
        
        if risk_score > self.risk_threshold:
            alerts.append(f"Soglia rischio superata: {risk_score:.4f}/{self.risk_threshold}")
        
        if alerts:
            return True, "; ".join(alerts)
        
        return False, "Tutto nella norma"
    
    def _send_alert(self, message: str, risk_score: float, stations_count: int) -> bool:
        """Invia un allarme tramite AlertSystem."""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Alert would be sent: {message}")
            return True
        
        if not self.alert_system:
            self.logger.error("Alert system not initialized")
            return False
        
        try:
            alert_message = AlertMessage(
                title="Allarme Campi Flegrei",
                message=message,
                severity="high" if risk_score > self.risk_threshold * 1.5 else "medium",
                metadata={
                    "risk_score": round(risk_score, 4),
                    "stations_count": stations_count,
                    "threshold": self.risk_threshold,
                    "model_version": self.model_version,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            result = self.alert_system.send_alert(alert_message)
            self.logger.info(f"Alert sent: {result}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending alert: {e}")
            return False
    
    def _save_results(self, df: pd.DataFrame, risk_score: float, alert_required: bool, alert_message: str) -> None:
        """Salva i risultati del monitoraggio."""
        results_dir = PROJECT_ROOT / "runs/monitor"
        results_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        df.to_csv(results_dir / f"waveforms_{timestamp}.csv", index=False)
        
        import json
        summary = {
            "timestamp": datetime.now().isoformat(),
            "risk_score": risk_score,
            "alert_required": alert_required,
            "alert_message": alert_message,
            "stations_count": len(df["station"].unique()),
            "records_count": len(df),
            "model_version": self.model_version
        }
        
        with open(results_dir / f"summary_{timestamp}.json", "w") as f:
            json.dump(summary, f, indent=2)
        
        self.logger.info(f"Results saved to {results_dir}")
    
    def run_once(self) -> bool:
        """Esegue un ciclo di monitoraggio."""
        self.logger.info("=" * 60)
        self.logger.info(f"Starting monitoring cycle at {datetime.now().isoformat()}")
        self.logger.info("=" * 60)
        
        try:
            stations = self._get_stations_in_area()
            if len(stations) < self.min_stations:
                self.logger.warning(f"Only {len(stations)} stations available, need {self.min_stations}")
            
            df = self._fetch_latest_waveforms(stations, minutes=15)
            if df is None or len(df) == 0:
                self.logger.warning("No waveform data available")
                return False
            
            df_processed = self._preprocess_data(df)
            risk_score = self._predict_risk(df_processed)
            alert_required, alert_message = self._check_thresholds(risk_score, len(stations))
            
            if alert_required:
                self._send_alert(alert_message, risk_score, len(stations))
            else:
                self.logger.info(alert_message)
            
            self._save_results(df_processed, risk_score, alert_required, alert_message)
            
            self.logger.info("=" * 60)
            self.logger.info("Monitoring cycle completed successfully")
            self.logger.info("=" * 60)
            return True
            
        except Exception as e:
            self.logger.error(f"Error in monitoring cycle: {e}", exc_info=True)
            return False
    
    def run(self) -> None:
        """Esegue il monitoraggio in loop continuo."""
        self.logger.info("Starting Campi Flegrei Real-Time Monitor")
        self.logger.info(f"Press Ctrl+C to stop")
        
        while not shutdown_requested:
            try:
                success = self.run_once()
                
                if not success and not self.daemon:
                    self.logger.error("Monitoring cycle failed, exiting...")
                    break
                
                self.logger.info(f"Next cycle in {self.interval_seconds} seconds...")
                
                sleep_remaining = self.interval_seconds
                while sleep_remaining > 0 and not shutdown_requested:
                    time.sleep(min(1, sleep_remaining))
                    sleep_remaining -= 1
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}", exc_info=True)
                if not self.daemon:
                    break
                time.sleep(60)
        
        self.logger.info("Monitor stopped")


def main():
    parser = argparse.ArgumentParser(description="Campi Flegrei Real-Time Monitor")
    parser.add_argument("--config", type=Path, help="Path to config file")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MINUTES,
                        help="Monitoring interval in minutes")
    parser.add_argument("--min-stations", type=int, default=DEFAULT_MIN_STATIONS,
                        help="Minimum number of stations required")
    parser.add_argument("--risk-threshold", type=float, default=DEFAULT_RISK_THRESHOLD,
                        help="Risk threshold for alerts")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test mode (no alerts sent)")
    parser.add_argument("--daemon", action="store_true",
                        help="Daemon mode (auto-restart on error)")
    parser.add_argument("--once", action="store_true",
                        help="Run once and exit")
    parser.add_argument("--model-version", type=str, default=None,
                        help="Model version to use (default: current)")
    
    args = parser.parse_args()
    
    interval = int(os.getenv("MONITOR_INTERVAL", str(args.interval)))
    min_stations = int(os.getenv("MONITOR_MIN_STATIONS", str(args.min_stations)))
    risk_threshold = float(os.getenv("MONITOR_RISK_THRESHOLD", str(args.risk_threshold)))
    model_version = args.model_version or os.getenv("MODEL_VERSION")
    
    monitor = CampiFlegreiMonitor(
        config_path=args.config,
        interval_minutes=interval,
        min_stations=min_stations,
        risk_threshold=risk_threshold,
        dry_run=args.dry_run,
        daemon=args.daemon,
        model_version=model_version
    )
    
    if args.once:
        monitor.run_once()
    else:
        monitor.run()


if __name__ == "__main__":
    main()
