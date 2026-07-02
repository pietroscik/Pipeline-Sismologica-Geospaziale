"""

Model Versioning System - Issue #6

Sistema di versioning e tracking per modelli ML con integrazione MLflow.

"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

import mlflow
import logging

from path_utils import PROJECT_ROOT

MODEL_REGISTRY_DIR = PROJECT_ROOT / "models" / "registry"
MODEL_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

DEFAULT_EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT_NAME", "seismic-risk-model")
DEFAULT_TRACKING_URI = os.getenv(
    "MLFLOW_TRACKING_URI",
    f"sqlite:///{(PROJECT_ROOT / 'mlflow.db').as_posix()}",
)

def configure_mlflow() -> None:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", DEFAULT_TRACKING_URI)

    # Compatibilità opzionale con vecchio file store
    if tracking_uri.startswith("file:"):
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        logger.warning("Using MLflow file store in compatibility mode.")

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", DEFAULT_EXPERIMENT))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _get_registry_dir() -> Path:
    MODEL_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    return MODEL_REGISTRY_DIR


def build_model_version(model_name: str) -> str:
    return f"{model_name}_{_utc_now()}"


def save_local_model_manifest(
    *,
    model_name: str,
    version: str,
    metrics: dict[str, float],
    params: dict[str, Any],
    artifact_path: str,
    run_id: str | None = None,
) -> Path:
    registry_dir = _get_registry_dir()
    manifest_path = registry_dir / f"{version}.json"
    payload = {
        "model_name": model_name,
        "version": version,
        "created_at_utc": _utc_now(),
        "metrics": metrics,
        "params": params,
        "artifact_path": artifact_path,
        "run_id": run_id,
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest_path


def log_training_run(
    *,
    model_name: str,
    params: dict[str, Any],
    metrics: dict[str, float],
    artifacts: list[str] | None = None,
) -> dict[str, str]:
    """
    Esegue tracking MLflow + salva manifest locale versionato.
    Ritorna: {"run_id": ..., "version": ..., "manifest": ...}
    """
    configure_mlflow()
    version = build_model_version(model_name)

    with mlflow.start_run(run_name=version) as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)

        if artifacts:
            for artifact in artifacts:
                if artifact and Path(artifact).exists():
                    mlflow.log_artifact(artifact)

        manifest = save_local_model_manifest(
            model_name=model_name,
            version=version,
            metrics=metrics,
            params=params,
            artifact_path=artifacts[0] if artifacts else "",
            run_id=run.info.run_id,
        )

        mlflow.log_artifact(str(manifest), artifact_path="model_registry")

        return {
            "run_id": run.info.run_id,
            "version": version,
            "manifest": str(manifest),
        }


class ModelVersionManager:
    """Gestisce il versioning e tracking dei modelli ML."""

    def __init__(
        self,
        model_type: str,
        base_dir: Optional[Path] = None,
        use_mlflow: bool = True,
        mlflow_tracking_uri: str | None = None,
        experiment_name: str | None = None,
    ):

        self.model_type = model_type

        self.base_dir = base_dir or PROJECT_ROOT / "mobile" / "models"

        self.models_dir = self.base_dir / model_type

        self.use_mlflow = use_mlflow
        self.mlflow_tracking_uri = (
            mlflow_tracking_uri
            or os.getenv("MLFLOW_TRACKING_URI")
            or "sqlite:///mlflow.db"
        )
        self.experiment_name = (
            experiment_name
            or os.getenv("MLFLOW_EXPERIMENT_NAME")
            or "seismic-risk-model"
        )

        self.models_dir.mkdir(parents=True, exist_ok=True)

        if self.use_mlflow:

            self._init_mlflow()

        logger.info(
            f"ModelVersionManager initialized for {model_type} at {self.models_dir}"
        )

    def _init_mlflow(self) -> None:
        """Inizializza MLflow."""

        try:

            import mlflow
            import mlflow.sklearn

            mlflow.set_tracking_uri(self.mlflow_tracking_uri)
            if self.mlflow_tracking_uri.startswith("file:"):
                os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
                logger.warning("Using MLflow file store compatibility mode.")

            if not mlflow.get_experiment_by_name(self.experiment_name):
                mlflow.create_experiment(self.experiment_name)
            mlflow.set_experiment(self.experiment_name)

            logger.info(f"MLflow initialized for experiment: {self.experiment_name}")

        except ImportError:

            logger.warning("MLflow not installed. Using local versioning only.")

            self.use_mlflow = False

        except Exception as e:

            logger.error(f"Error initializing MLflow: {e}")

            self.use_mlflow = False

    def _generate_version(self) -> str:
        """Genera un nome versione basato sulla data."""

        today = datetime.now().strftime("%Y%m%d")

        existing = [d.name for d in self.models_dir.iterdir() if d.is_dir()]

        today_versions = [v for v in existing if v.startswith(f"v1_{today}")]

        if today_versions:

            max_num = max([int(v.split("_")[0][1:]) for v in today_versions])

            return f"v{max_num + 1}_{today}"

        return f"v1_{today}"

    def _get_version_dir(self, version: str) -> Path:
        """Ottieni il path della directory di una versione."""

        return self.models_dir / version

    def save_model(
        self,
        model: Any,
        metadata: Optional[Dict[str, Any]] = None,
        performance: Optional[Dict[str, float]] = None,
        version: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Salva un modello con versioning e tracking."""

        if version is None:

            version = self._generate_version()

        version_dir = self._get_version_dir(version)

        version_dir.mkdir(parents=True, exist_ok=True)

        import joblib

        model_path = version_dir / "model.joblib"

        joblib.dump(model, model_path)

        if metadata is None:

            metadata = {}

        metadata.update(
            {
                "version": version,
                "model_type": self.model_type,
                "saved_at": datetime.now().isoformat(),
                "file_size": model_path.stat().st_size,
            }
        )

        with open(version_dir / "metadata.json", "w") as f:

            json.dump(metadata, f, indent=2)

        if performance is not None:

            with open(version_dir / "performance.json", "w") as f:

                json.dump(performance, f, indent=2)

        if self.use_mlflow:

            try:

                import mlflow
                import mlflow.sklearn

                with mlflow.start_run(run_name=version):

                    mlflow.sklearn.log_model(model, name="model")

                    if params:

                        mlflow.log_params(params)

                    if performance:

                        mlflow.log_metrics(performance)

                    if tags:

                        mlflow.set_tags(tags)

                    for key, value in metadata.items():

                        if isinstance(value, (str, int, float, bool)):

                            mlflow.log_param(key, str(value))

            except Exception as e:

                logger.error(f"Error logging to MLflow: {e}")

        current_link = self.models_dir / "current"

        if current_link.exists():

            current_link.unlink()

        current_link.symlink_to(version, target_is_directory=True)

        logger.info(f"Model saved as version: {version}")

        return version

    def load_model(self, version: Optional[str] = None) -> Tuple[Any, Dict[str, Any]]:
        """Carica un modello e i suoi metadata."""

        if version is None:

            version = "current"

        version_path = self._get_version_dir(version)

        if not version_path.exists():

            all_versions = [d.name for d in self.models_dir.iterdir() if d.is_dir()]

            if version in all_versions:

                version_path = self.models_dir / version

            else:

                raise ValueError(
                    f"Version {version} not found. Available: {all_versions}"
                )

        import joblib

        model_path = version_path / "model.joblib"

        model = joblib.load(model_path)

        metadata_path = version_path / "metadata.json"

        metadata = {}

        if metadata_path.exists():

            with open(metadata_path, "r") as f:

                metadata = json.load(f)

        logger.info(f"Model loaded: {version}")

        return model, metadata

    def list_versions(self) -> List[Dict[str, Any]]:
        """Elenca tutte le versioni disponibili."""

        versions = []

        for version_dir in sorted(self.models_dir.iterdir()):

            if version_dir.is_dir() and version_dir.name != "current":

                metadata_path = version_dir / "metadata.json"

                metadata = {}

                if metadata_path.exists():

                    with open(metadata_path, "r") as f:

                        metadata = json.load(f)

                versions.append(
                    {
                        "version": version_dir.name,
                        "path": str(version_dir),
                        "metadata": metadata,
                        "is_current": (self.models_dir / "current").resolve()
                        == version_dir.resolve(),
                    }
                )

        versions.sort(key=lambda x: x["version"], reverse=True)

        return versions

    def get_latest_version(self) -> Optional[str]:
        """Ottieni l'ultima versione salvata."""

        versions = self.list_versions()

        if versions:

            return versions[0]["version"]

        return None

    def rollback(self, version: str) -> bool:
        """Effettua il rollback a una versione specifica."""

        version_dir = self._get_version_dir(version)

        if not version_dir.exists():

            logger.error(f"Version {version} not found for rollback")

            return False

        current_link = self.models_dir / "current"

        if current_link.exists():

            current_link.unlink()

        current_link.symlink_to(version, target_is_directory=True)

        logger.info(f"Rolled back to version: {version}")

        return True

    def delete_version(self, version: str) -> bool:
        """Elimina una versione."""

        version_dir = self._get_version_dir(version)

        if not version_dir.exists():

            logger.error(f"Version {version} not found for deletion")

            return False

        shutil.rmtree(version_dir)

        current_link = self.models_dir / "current"

        if current_link.exists() and current_link.resolve() == version_dir:

            latest = self.get_latest_version()

            if latest:

                self.rollback(latest)

        logger.info(f"Deleted version: {version}")

        return True

    def compare_versions(self, version1: str, version2: str) -> Dict[str, Any]:
        """Confronta due versioni di modelli."""

        model1, meta1 = self.load_model(version1)

        model2, meta2 = self.load_model(version2)

        perf1_path = self._get_version_dir(version1) / "performance.json"

        perf2_path = self._get_version_dir(version2) / "performance.json"

        perf1 = {}

        perf2 = {}

        if perf1_path.exists():

            with open(perf1_path, "r") as f:

                perf1 = json.load(f)

        if perf2_path.exists():

            with open(perf2_path, "r") as f:

                perf2 = json.load(f)

        comparison = {
            "version1": version1,
            "version2": version2,
            "metadata_diff": {
                k: (meta1.get(k), meta2.get(k))
                for k in set(meta1.keys()) | set(meta2.keys())
            },
            "performance_diff": {
                k: (perf1.get(k), perf2.get(k))
                for k in set(perf1.keys()) | set(perf2.keys())
            },
        }

        comparison["performance_change"] = {}

        for metric in set(perf1.keys()) & set(perf2.keys()):

            if isinstance(perf1[metric], (int, float)) and isinstance(
                perf2[metric], (int, float)
            ):

                change = perf2[metric] - perf1[metric]

                pct_change = (change / perf1[metric] * 100) if perf1[metric] != 0 else 0

                is_improvement = (
                    change > 0
                    if metric in ["accuracy", "r2", "precision", "recall", "f1"]
                    else change < 0
                )

                comparison["performance_change"][metric] = {
                    "absolute": change,
                    "percentage": pct_change,
                    "improved": is_improvement,
                }

        return comparison

    def get_model_info(self, version: Optional[str] = None) -> Dict[str, Any]:
        """Ottieni informazioni complete su una versione."""

        if version is None:

            version = "current"

        version_dir = self._get_version_dir(version)

        if not version_dir.exists():

            raise ValueError(f"Version {version} not found")

        info = {
            "version": version,
            "path": str(version_dir),
            "files": [f.name for f in version_dir.iterdir() if f.is_file()],
        }

        metadata_path = version_dir / "metadata.json"

        if metadata_path.exists():

            with open(metadata_path, "r") as f:

                info["metadata"] = json.load(f)

        performance_path = version_dir / "performance.json"

        if performance_path.exists():

            with open(performance_path, "r") as f:

                info["performance"] = json.load(f)

        return info


def get_model_manager(model_type: str = "default") -> ModelVersionManager:
    """Ottieni un ModelVersionManager per un tipo di modello."""

    return ModelVersionManager(model_type=model_type)


def save_model(
    model: Any,
    model_type: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
    performance: Optional[Dict[str, float]] = None,
    version: Optional[str] = None,
) -> str:
    """Salva un modello con versioning."""

    manager = get_model_manager(model_type)

    return manager.save_model(model, metadata, performance, version)


def load_model(
    model_type: str = "default", version: Optional[str] = None
) -> Tuple[Any, Dict[str, Any]]:
    """Carica un modello."""

    manager = get_model_manager(model_type)

    return manager.load_model(version)
