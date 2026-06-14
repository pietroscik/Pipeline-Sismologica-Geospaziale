#!/usr/bin/env python3
"""
Retraining Automatico Modelli ML
Script principale per il retraining automatico dei modelli di machine learning.
Implementa trigger multipli (temporale, dati, prestazioni) e deployment automatico.
Issue #7: Retraining Automatico Modelli ML
"""
import argparse
import logging
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
import joblib

CONFIG_PATH = Path(__file__).parent / "config" / "retraining_config.yaml"

class RetrainingConfig:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_default_config()
        self._load_config()
    
    def _load_default_config(self):
        return {
            "retraining": {"enabled": True, "schedule": "weekly", "interval_seconds": 604800,
                "min_new_samples": 1000, "performance_threshold": 0.85,
                "models_to_retrain": ["xgboost", "random_forest"], "cross_validation_folds": 5, "test_size": 0.2},
            "data": {"input_dir": "examples/mobile_devices", "input_file": "scoperte_automatiche.csv.gz", "min_samples": 1000},
            "notification": {"enabled": True, "channels": ["console", "file", "slack"], "success": True, "failure": True},
            "logging": {"level": "INFO", "file": "logs/retraining.log"}}
    
    def _load_config(self):
        try:
            if self.config_path.exists():
                with open(self.config_path, "r") as f:
                    file_config = yaml.safe_load(f)
                    if file_config:
                        self.config = self._deep_merge(self.config, file_config)
        except Exception as e:
            logging.error(f"Error loading config: {e}")
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def get(self, *keys, default=None):
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

class DataLoader:
    def __init__(self, config: RetrainingConfig):
        self.config = config
    
    def load_data(self):
        input_path = Path(self.config.get("data", "input_dir")) / self.config.get("data", "input_file")
        if not input_path.exists():
            raise FileNotFoundError(f"Data file not found: {input_path}")
        df = pd.read_csv(input_path) if self.config.get("data", "input_file").endswith((".csv.gz", ".csv")) else pd.read_parquet(input_path)
        return df.drop(columns=[df.columns[-1]]), df[df.columns[-1]]

class ModelTrainer:
    MODEL_CLASSES = {"random_forest": RandomForestRegressor, "xgboost": GradientBoostingRegressor}
    
    def __init__(self, config: RetrainingConfig):
        self.config = config
    
    def train(self, model_type: str, X_train, y_train):
        if model_type not in self.MODEL_CLASSES:
            raise ValueError(f"Unknown model type: {model_type}")
        return self.MODEL_CLASSES[model_type]().fit(X_train, y_train)
    
    def cross_validate(self, model, X, y) -> Dict:
        n_folds = self.config.get("retraining", "cross_validation_folds", 5)
        cv_scores = cross_val_score(model, X, y, cv=n_folds, scoring="r2")
        return {"cv_scores": cv_scores.tolist(), "mean_cv_score": float(np.mean(cv_scores)), "std_cv_score": float(np.std(cv_scores))}

class ModelEvaluator:
    def __init__(self, config: RetrainingConfig):
        self.config = config
    
    def evaluate(self, model, X_test, y_test) -> Dict:
        y_pred = model.predict(X_test)
        return {"r2": float(r2_score(y_test, y_pred)), "mse": float(mean_squared_error(y_test, y_pred)),
                "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))), "mae": float(mean_absolute_error(y_test, y_pred))}
    
    def should_deploy(self, metrics: Dict, threshold: float = 0.85) -> bool:
        return metrics.get("r2", 0) >= threshold

class FallbackModelVersionManager:
    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
    
    def get_latest_version(self, model_type: str) -> Optional[str]:
        model_dir = self.models_dir / model_type
        if not model_dir.exists():
            return None
        versions = sorted([d.name for d in model_dir.iterdir() if d.is_dir()])
        return versions[-1] if versions else None
    
    def get_next_version(self, model_type: str) -> str:
        latest = self.get_latest_version(model_type)
        if latest is None:
            return "v1.0.0"
        parts = latest.lstrip("v").split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        return "v" + ".".join(parts)
    
    def save_model(self, model, model_type: str, version: str, metrics: Dict) -> Path:
        version_dir = self.models_dir / model_type / version
        version_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, version_dir / "model.joblib")
        with open(version_dir / "metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)
        return version_dir / "model.joblib"
    
    def load_model(self, model_type: str, version: str):
        return joblib.load(self.models_dir / model_type / version / "model.joblib")

class FallbackAlertSystem:
    def __init__(self, config: RetrainingConfig):
        self.config = config
        self.logger = logging.getLogger("alert_system")
    
    def send_alert(self, message: str, alert_type: str = "info"):
        if alert_type == "success" and not self.config.get("notification", "success", True):
            return
        if alert_type == "failure" and not self.config.get("notification", "failure", True):
            return
        self.logger.info(f"[{alert_type.upper()}] {message}")

class RetrainingOrchestrator:
    def __init__(self, config: RetrainingConfig):
        self.config = config
        self.data_loader = DataLoader(config)
        self.model_trainer = ModelTrainer(config)
        self.model_evaluator = ModelEvaluator(config)
        try:
            from model_version_manager import ModelVersionManager
            self.model_version_manager = ModelVersionManager(models_dir=Path("mobile") / "models")
        except ImportError:
            self.model_version_manager = FallbackModelVersionManager(models_dir=Path("mobile") / "models")
        try:
            from alert_system import AlertSystem
            self.alert_system = AlertSystem()
        except ImportError:
            self.alert_system = FallbackAlertSystem(config)
        self.runs_dir = Path("runs") / "retraining"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
    
    def check_triggers(self) -> List[str]:
        models = []
        rc = self.config.config.get("retraining", {})
        if not rc.get("enabled", True):
            return models
        if self._check_temporal_trigger():
            models = rc.get("models_to_retrain", [])
        if self._check_data_trigger(rc.get("min_new_samples", 1000)):
            models = rc.get("models_to_retrain", [])
        if self._check_performance_trigger():
            models = rc.get("models_to_retrain", [])
        return list(set(models))
    
    def _check_temporal_trigger(self) -> bool:
        interval = self.config.get("retraining", "interval_seconds", 604800)
        last_run_file = self.runs_dir / "last_run.txt"
        if last_run_file.exists():
            try:
                last_run = datetime.fromisoformat(open(last_run_file).read().strip())
                return (datetime.now() - last_run).total_seconds() >= interval
            except:
                pass
        return True
    
    def _check_data_trigger(self, min_new: int) -> bool:
        try:
            X, _ = self.data_loader.load_data()
            count_file = self.runs_dir / "last_count.txt"
            prev = int(open(count_file).read().strip()) if count_file.exists() else 0
            open(count_file, "w").write(str(len(X)))
            return (len(X) - prev) >= min_new
        except:
            return False
    
    def _check_performance_trigger(self) -> bool:
        threshold = self.config.get("retraining", "performance_threshold", 0.85)
        for mt in self.config.get("retraining", "models_to_retrain", []):
            try:
                latest = self.model_version_manager.get_latest_version(mt)
                if latest:
                    with open(self.models_dir / mt / latest / "metrics.json") as f:
                        if json.load(f).get("r2", 0) < threshold:
                            return True
            except:
                pass
        return False
    
    def retrain_model(self, model_type: str) -> Dict:
        result = {"model_type": model_type, "status": "pending", "timestamp": datetime.now().isoformat()}
        try:
            X, y = self.data_loader.load_data()
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=self.config.get("retraining", "test_size", 0.2), random_state=42)
            new_model = self.model_trainer.train(model_type, X_train, y_train)
            new_metrics = {**self.model_evaluator.evaluate(new_model, X_test, y_test), **self.model_trainer.cross_validate(new_model, X_train, y_train)}
            if self.model_evaluator.should_deploy(new_metrics, self.config.get("retraining", "performance_threshold", 0.85)):
                next_ver = self.model_version_manager.get_next_version(model_type)
                self.model_version_manager.save_model(new_model, model_type, next_ver, new_metrics)
                result.update({"deployed": True, "new_version": next_ver})
            else:
                result["deployed"] = False
            result.update({"status": "success", "metrics": new_metrics})
            if self.config.get("notification", "enabled", True):
                msg = f"Retraining completed for {model_type}: R2={new_metrics['r2']:.4f}"
                if result.get("deployed"):
                    msg += f" - Deployed {next_ver}"
                self.alert_system.send_alert(msg, "success")
        except Exception as e:
            result.update({"status": "failure", "error": str(e)})
            if self.config.get("notification", "enabled", True):
                self.alert_system.send_alert(f"Retraining failed for {model_type}: {e}", "failure")
        with open(self.runs_dir / f"retraining_{model_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json", "w") as f:
            json.dump(result, f, indent=2)
        with open(self.runs_dir / "last_run.txt", "w") as f:
            f.write(datetime.now().isoformat())
        return result
    
    def retrain_all(self, model_types: Optional[List[str]] = None) -> Dict:
        if model_types is None:
            model_types = self.config.get("retraining", "models_to_retrain", [])
        overall = {"timestamp": datetime.now().isoformat(), "models": {}, "summary": {"total": len(model_types), "success": 0, "failure": 0, "deployed": 0}}
        for mt in model_types:
            try:
                result = self.retrain_model(mt)
                overall["models"][mt] = result
                if result["status"] == "success":
                    overall["summary"]["success"] += 1
                    if result.get("deployed"):
                        overall["summary"]["deployed"] += 1
                else:
                    overall["summary"]["failure"] += 1
            except Exception as e:
                overall["models"][mt] = {"status": "failure", "error": str(e)}
                overall["summary"]["failure"] += 1
        return overall
    
    def run_continuous(self, interval: Optional[int] = None):
        interval = interval or self.config.get("retraining", "interval_seconds", 604800)
        while True:
            try:
                models = self.check_triggers()
                if models:
                    self.retrain_all(models)
                time.sleep(interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(f"Error in continuous mode: {e}")
                time.sleep(60)

def main():
    parser = argparse.ArgumentParser(description="Retraining Automatico Modelli ML - Issue #7")
    parser.add_argument("--model-type", type=str, help="Tipo modello")
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--interval", type=int, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = RetrainingConfig(Path(args.config) if args.config else None)
    orchestrator = RetrainingOrchestrator(config)
    if args.daemon or args.continuous:
        orchestrator.run_continuous(args.interval)
    elif args.model_type:
        logging.info(json.dumps(orchestrator.retrain_model(args.model_type), indent=2))
    else:
        models = orchestrator.check_triggers() if not args.force else config.get("retraining", "models_to_retrain", [])
        if not models:
            logging.info("No models to retrain. Use --force or --model-type")
            return
        logging.info(json.dumps(orchestrator.retrain_all(models)["summary"], indent=2))

if __name__ == "__main__":
    import time
    main()
