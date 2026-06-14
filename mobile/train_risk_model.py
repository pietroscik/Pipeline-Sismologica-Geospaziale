#!/usr/bin/env python3
"""
Addestramento Modello ML per Monitoraggio Campi Flegrei - Issue #4

Script per addestrare un modello di predizione del rischio sismico
usando i dati storici disponibili.

Usage:
    # Addestramento con dati default
    python mobile/train_risk_model.py
    
    # Addestramento con file custom
    python mobile/train_risk_model.py --input examples/mobile_devices/scoperte_automatiche.csv.gz
    
    # Addestramento con parametri custom
    python mobile/train_risk_model.py --model-type xgboost --test-size 0.3

Output:
    mobile/models/campi_flegrei_risk_model.pkl
    mobile/models/training_report.json
"""

import argparse
import os
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
import joblib

# Import PROJECT_ROOT for consistent path resolution
from path_utils import PROJECT_ROOT


# Constants
DEFAULT_INPUT = PROJECT_ROOT / "examples/mobile_devices/scoperte_automatiche.csv.gz"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "mobile/models/campi_flegrei_risk_model.pkl"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "mobile/models/training_report.json"

# Feature columns (deve corrispondere a monitor_config.yaml)
FEATURE_COLUMNS = ["mean", "std", "min", "max", "amplitude_range", "hour", "minute"]


def load_data(input_path: Path) -> pd.DataFrame:
    """Carica i dati da file CSV."""
    print(f"Loading data from: {input_path}")
    
    if input_path.suffix == ".gz":
        df = pd.read_csv(input_path, compression="gzip")
    else:
        df = pd.read_csv(input_path)
    
    print(f"Loaded {len(df)} records")
    print(f"Columns: {list(df.columns)}")
    return df


def preprocess_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Pre-elabora i dati per l'addestramento.
    
    Returns:
        tuple: (features, target)
    """
    # Make a copy to avoid modifying original
    df = df.copy()
    
    # Convert timestamp if exists
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["hour"] = df["timestamp"].dt.hour
        df["minute"] = df["timestamp"].dt.minute
    elif "starttime" in df.columns:
        df["starttime"] = pd.to_datetime(df["starttime"])
        df["hour"] = df["starttime"].dt.hour
        df["minute"] = df["starttime"].dt.minute
    else:
        # If no timestamp, use random hour/minute for training
        np.random.seed(42)
        df["hour"] = np.random.randint(0, 24, size=len(df))
        df["minute"] = np.random.randint(0, 60, size=len(df))
    
    # Calculate derived features
    if "max" in df.columns and "min" in df.columns:
        df["amplitude_range"] = df["max"] - df["min"]
    else:
        # Create synthetic amplitude_range if not exists
        df["amplitude_range"] = df["std"] * 2 if "std" in df.columns else 1.0
    
    # Select features
    available_features = [f for f in FEATURE_COLUMNS if f in df.columns]
    
    if not available_features:
        raise ValueError(f"No feature columns found. Available: {list(df.columns)}")
    
    features = df[available_features]
    
    # Create target: normalized risk score (0-1)
    # If 'delta' or 'risk' column exists, use it
    if "delta" in df.columns:
        # Normalize delta to 0-1 range
        delta_min = df["delta"].min()
        delta_max = df["delta"].max()
        target = (df["delta"] - delta_min) / (delta_max - delta_min + 1e-10)
    elif "risk" in df.columns:
        target = df["risk"]
    else:
        # Create synthetic target based on std and amplitude
        target = (df["std"] / df["std"].max()) * 0.5 + (df["amplitude_range"] / df["amplitude_range"].max()) * 0.5
    
    return features, target


def train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    model_type: str = "random_forest",
    random_state: int = 42
) -> Any:
    """
    Addestra un modello di Machine Learning.
    
    Args:
        X_train: Feature matrix
        y_train: Target vector
        model_type: Tipo di modello ('random_forest', 'xgboost')
        random_state: Seed per riproducibilita
        
    Returns:
        Modello addestrato
    """
    if model_type == "xgboost":
        try:
            from xgboost import XGBRegressor
            model = XGBRegressor(
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                random_state=random_state
            )
        except ImportError:
            print("XGBoost not available, falling back to Random Forest")
            model_type = "random_forest"
    
    if model_type == "random_forest":
        model = RandomForestRegressor(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            random_state=random_state,
            n_jobs=-1
        )
    else:
        raise ValueError(f"Unknown model type: {model_type}")
    
    print(f"Training {model_type} model...")
    model.fit(X_train, y_train)
    print("Training completed!")
    
    return model


def evaluate_model(model, X_test: pd.DataFrame, y_test: pd.Series) -> Dict[str, float]:
    """Valuta il modello su dati di test."""
    y_pred = model.predict(X_test)
    
    metrics = {
        "mse": float(mean_squared_error(y_test, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
        "mae": float(mean_absolute_error(y_test, y_pred)),
        "r2": float(r2_score(y_test, y_pred)),
        "max_error": float(max(abs(y_test - y_pred)))
    }
    
    return metrics


def save_model(model, model_path: Path) -> None:
    """Salva il modello su disco."""
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    print(f"Model saved to: {model_path}")


def save_report(
    report: Dict[str, Any],
    report_path: Path,
    model_type: str,
    training_time: float
) -> None:
    """Salva il report di addestramento."""
    report["metadata"] = {
        "timestamp": datetime.now().isoformat(),
        "model_type": model_type,
        "training_time_seconds": training_time,
        "feature_columns": FEATURE_COLUMNS
    }
    
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"Report saved to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Train ML model for Campi Flegrei monitoring")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Input CSV file with training data")
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL_PATH,
                        help="Output path for trained model")
    parser.add_argument("--model-type", type=str, default="random_forest",
                        choices=["random_forest", "xgboost"],
                        help="Type of ML model to train")
    parser.add_argument("--test-size", type=float, default=0.2,
                        help="Proportion of data for testing")
    parser.add_argument("--random-state", type=int, default=42,
                        help="Random seed for reproducibility")
    parser.add_argument("--force", action="store_true",
                        help="Force retraining even if model exists")
    
    args = parser.parse_args()
    
    # Check if model already exists
    if args.output.exists() and not args.force:
        print(f"Model already exists at {args.output}")
        print("Use --force to retrain")
        return
    
    # Load data
    start_time = datetime.now()
    df = load_data(args.input)
    
    # Preprocess
    features, target = preprocess_data(df)
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        features, target,
        test_size=args.test_size,
        random_state=args.random_state
    )
    
    print(f"Training set: {len(X_train)} samples")
    print(f"Test set: {len(X_test)} samples")
    
    # Train model
    train_start = datetime.now()
    model = train_model(X_train, y_train, args.model_type, args.random_state)
    training_time = (datetime.now() - train_start).total_seconds()
    
    # Evaluate
    metrics = evaluate_model(model, X_test, y_test)
    print("
Evaluation Metrics:")
    for metric, value in metrics.items():
        print(f"  {metric.upper()}: {value:.4f}")
    
    # Save model
    save_model(model, args.output)
    
    # Save report
    report = {
        "metrics": metrics,
        "training": {
            "samples": len(X_train),
            "features": list(features.columns),
            "target_range": {
                "min": float(target.min()),
                "max": float(target.max()),
                "mean": float(target.mean())
            }
        },
        "test": {
            "samples": len(X_test)
        }
    }
    
    save_report(report, DEFAULT_REPORT_PATH, args.model_type, training_time)
    
    total_time = (datetime.now() - start_time).total_seconds()
    print(f"
Total time: {total_time:.2f} seconds")
    print("
Model ready for use with monitor_campi_flegrei.py!")


if __name__ == "__main__":
    main()