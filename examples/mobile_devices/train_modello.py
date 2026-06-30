import pandas as pd
import numpy as np
import time
import logging
import joblib
import json
from imblearn.over_sampling import SMOTE
from pathlib import Path
from typing import Tuple, Dict, Optional, Any
from datetime import datetime

# Importa sistema di allarme e configurazione
import sys
# Risaliamo di 3 livelli: train_modello.py -> mobile_devices -> examples -> root -> mobile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "mobile"))
from alert_system import AlertSystem, get_alert_system
from logging_config import setup_logging
from data_validator import (
    validate_csv_file,
    validate_data,
    DataValidationError
)

# Configura logging
logger = logging.getLogger(__name__)

# Costanti
DEFAULT_MODEL_TYPE = "xgboost"
DEFAULT_MIN_STATIONS = 18
DEFAULT_ALERT_THRESHOLD = 0.7


def load_data(dataset_path: str = "dataset_ml_sismico.csv") -> pd.DataFrame:
    """Carica il dataset per il training."""
    logger.info(f"📖 Caricamento dataset da {dataset_path}...")
    
    dataset_path = Path(dataset_path)
    
    # Validate file exists and is readable
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset file non trovato: {dataset_path}")
    if dataset_path.stat().st_size == 0:
        raise DataValidationError("Dataset file e' vuoto", errors=["Dataset file is empty"])
    
    # Load and validate CSV
    try:
        df = validate_csv_file(
            dataset_path,
            required_columns={'Target_Allarme'}
        )
    except DataValidationError as e:
        logger.error(f"❌ Validazione dataset fallita: {e.message}")
        for err in e.errors:
            logger.error(f"   {err}")
        raise
    
    # Convert datetime index
    if 'Tempo' in df.columns:
        df['Tempo'] = pd.to_datetime(df['Tempo'])
        df.set_index('Tempo', inplace=True)
    elif df.index.name is None or df.index.name == 'Unnamed: 0':
        # Try to find datetime column
        datetime_cols = [col for col in df.columns if 'time' in col.lower() or 'tempo' in col.lower()]
        if datetime_cols:
            df[datetime_cols[0]] = pd.to_datetime(df[datetime_cols[0]])
            df.set_index(datetime_cols[0], inplace=True)
        else:
            logger.warning("Nessuna colonna temporale trovata, uso indice numerico")
    
    # Sort by index
    df.sort_index(inplace=True)
    
    # Check for empty DataFrame
    if len(df) == 0:
        raise DataValidationError("Dataset e' vuoto", errors=["Dataset contains no rows"])
    
    # Check for valid target values
    if 'Target_Allarme' not in df.columns:
        raise DataValidationError("Colonna Target_Allarme non trovata", errors=["Target column missing"])
    
    # Check class balance
    target_counts = df['Target_Allarme'].value_counts()
    logger.info(f"✅ Caricati {len(df)} record con {len(df.columns)} feature")
    logger.info(f"   Distribuzione target: {dict(target_counts)}")
    
    # Warn if dataset is too small
    if len(df) < 100:
        logger.warning(f"Dataset molto piccolo ({len(df)} record), risultati potrebbero non essere affidabili")
    
    return df

def split_data_temporal(df: pd.DataFrame, test_size: float = 0.2, random_state: int = 42) -> Tuple[pd.DataFrame, pd.DataFrame]:
    # IMPORTA TRAIN_TEST_SPLIT
    from sklearn.model_selection import train_test_split
    
    logger.info("⚖️ Esecuzione split stratificato per garantire presenza classi...")
    # Stratify garantisce che la proporzione di 0 e 1 sia mantenuta in train e test
    train, test = train_test_split(
        df, 
        test_size=test_size, 
        stratify=df['Target_Allarme'], 
        random_state=random_state
    )
    return train, test


def prepare_features(
    df: pd.DataFrame,
    target_column: str = "Target_Allarme",
    drop_columns: list = None
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Prepara feature matrix (X) e target vector (y).
    
    Args:
        df: DataFrame con feature e target
        target_column: Nome della colonna target
        drop_columns: Colonne da escludere (es. timestamp, ID)
    
    Returns:
        X, y: Feature matrix e target vector
    
    Raises:
        DataValidationError: Se ci sono problemi con i dati
    """
    if drop_columns is None:
        drop_columns = []
    
    # Check target column exists
    if target_column not in df.columns:
        raise DataValidationError(
            f"Colonna target '{target_column}' non trovata",
            errors=[f"Target column '{target_column}' not found in DataFrame"]
        )
    
    # Aggiungi colonne da escludere
    exclude = drop_columns + [target_column]
    
    # Seleziona feature
    feature_columns = [col for col in df.columns if col not in exclude]
    
    if len(feature_columns) == 0:
        raise DataValidationError(
            "Nessuna feature disponibile dopo esclusione colonne",
            errors=["No features available after excluding target and drop columns"]
        )
    
    X = df[feature_columns].copy()
    y = df[target_column].copy()
    
    # Check for NaN in target
    if y.isna().any():
        nan_count = y.isna().sum()
        logger.warning(f"{nan_count} valori NaN nella colonna target, saranno rimossi")
        # Remove rows with NaN in target
        valid_idx = y.notna()
        X = X[valid_idx]
        y = y[valid_idx]
    
    # Check for NaN in features
    nan_features = X.isna().any()
    if nan_features.any():
        nan_cols = nan_features[nan_features].index.tolist()
        logger.warning(f"Colonne con NaN: {nan_cols}")
    
    logger.info(f"⚙️  Feature selezionate: {len(feature_columns)}")
    logger.debug(f"Feature: {feature_columns}")
    
    return X, y

def apply_smote(X: pd.DataFrame, y: pd.Series) -> Tuple[pd.DataFrame, pd.Series]:
    """Applica SMOTE per bilanciare le classi."""
    logger.info("⚖️ Applicazione SMOTE per bilanciamento classi...")
    # Gestione NaN prima di SMOTE
    X = X.fillna(0)
    
    sm = SMOTE(random_state=42)
    X_res, y_res = sm.fit_resample(X, y)
    
    logger.info(f"   Originali: {len(X)}, Bilanciati: {len(X_res)}")
    return X_res, y_res


def train_xgboost(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame = None,
    y_test: pd.Series = None,
    early_stopping_rounds: int = 10,
    eval_metric: str = "aucpr",
    class_weight: str = "balanced",
    random_state: int = 42
) -> Tuple[Any, Dict]:
    """
    Addestra un modello XGBoost.
    
    Args:
        X_train: Feature matrix di training
        y_train: Target vector di training
        X_test: Feature matrix di test (opzionale, per early stopping)
        y_test: Target vector di test (opzionale, per early stopping)
        early_stopping_rounds: Numero di round senza miglioramento prima di fermarsi
        eval_metric: Metrica di valutazione
        class_weight: Pesi per classi sbilanciate
        random_state: Seed per riproducibilità
    
    Returns:
        model: Modello addestrato
        results: Dizionario con metriche e parametri
    
    Raises:
        ImportError: Se XGBoost non e' installato
        DataValidationError: Se ci sono problemi con i dati
    """
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("XGBoost non installato. Installa con: pip install xgboost")
        raise ImportError("XGBoost library not installed")
    
    logger.info("🌲 Addestramento modello XGBoost...")
    
    # Check for empty data
    if len(X_train) == 0 or len(y_train) == 0:
        raise DataValidationError("Dati di training vuoti")
    
    # Check for single class
    if len(y_train.unique()) < 2:
        raise DataValidationError(
            "Solo una classe nel target",
            errors=[f"Target has only one class: {y_train.unique()}"]
        )
    
    # Converte in DMatrix (formato ottimizzato per XGBoost)
    dtrain = xgb.DMatrix(X_train, label=y_train)
    
    # Parametri del modello
    params = {
        "objective": "binary:logistic",
        "eval_metric": eval_metric,
        "scale_pos_weight": len(y_train[y_train == 0]) / len(y_train[y_train == 1]) if len(y_train[y_train == 1]) > 0 else 1,
        "seed": random_state,
        "verbosity": 0
    }
    
    # Early stopping se dati di test forniti
    evals = []
    if X_test is not None and y_test is not None:
        if len(X_test) == 0 or len(y_test) == 0:
            logger.warning("Test set vuoto, early stopping disabilitato")
        else:
            dtest = xgb.DMatrix(X_test, label=y_test)
            evals = [(dtrain, "train"), (dtest, "eval")]
    else:
        evals = [(dtrain, "train")]
    
    # Addestramento
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=1000,
        early_stopping_rounds=early_stopping_rounds,
        evals=evals,
        verbose_eval=50
    )
    
    # Calcola metriche su training
    train_pred = model.predict(dtrain)
    train_results = calculate_metrics(y_train, train_pred)
    
    # Calcola metriche su test (se disponibile)
    test_results = {}
    if X_test is not None and y_test is not None:
        if len(X_test) > 0 and len(y_test) > 0:
            dtest = xgb.DMatrix(X_test, label=y_test)
            test_pred = model.predict(dtest)
            test_results = calculate_metrics(y_test, test_pred, prefix="test_")
    
    results = {
        "model_type": "xgboost",
        "params": params,
        "best_iteration": model.best_iteration,
        **train_results,
        **test_results
    }
    
    logger.info(f"✅ Modello XGBoost addestrato (iterazioni: {model.best_iteration})")
    return model, results


def train_random_forest(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame = None,
    y_test: pd.Series = None,
    n_estimators: int = 200,
    max_depth: int = 10,
    class_weight: str = "balanced",
    random_state: int = 42
) -> Tuple[Any, Dict]:
    """
    Addestra un modello Random Forest.
    
    Args:
        X_train: Feature matrix di training
        y_train: Target vector di training
        X_test: Feature matrix di test (opzionale)
        y_test: Target vector di test (opzionale)
        n_estimators: Numero di alberi
        max_depth: Profondità massima degli alberi
        class_weight: Pesi per classi sbilanciate
        random_state: Seed per riproducibilità
    
    Returns:
        model: Modello addestrato
        results: Dizionario con metriche e parametri
    
    Raises:
        ImportError: Se scikit-learn non e' installato
        DataValidationError: Se ci sono problemi con i dati
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
    except ImportError:
        logger.error("scikit-learn non installato. Installa con: pip install scikit-learn")
        raise ImportError("scikit-learn library not installed")
    
    logger.info("🌳 Addestramento modello Random Forest...")
    
    # Check for empty data
    if len(X_train) == 0 or len(y_train) == 0:
        raise DataValidationError("Dati di training vuoti")
    
    # Check for single class
    if len(y_train.unique()) < 2:
        raise DataValidationError(
            "Solo una classe nel target",
            errors=[f"Target has only one class: {y_train.unique()}"]
        )
    
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        class_weight=class_weight,
        random_state=random_state,
        n_jobs=-1,
        verbose=0
    )
    
    model.fit(X_train, y_train)
    
    # Calcola metriche su training
    train_pred = model.predict_proba(X_train)[:, 1]
    train_results = calculate_metrics(y_train, train_pred)
    
    # Calcola metriche su test (se disponibile)
    test_results = {}
    if X_test is not None and y_test is not None:
        if len(X_test) > 0 and len(y_test) > 0:
            test_pred = model.predict_proba(X_test)[:, 1]
            test_results = calculate_metrics(y_test, test_pred, prefix="test_")
    
    # Calcola feature importance
    feature_importance = dict(zip(X_train.columns, model.feature_importances_))
    
    results = {
        "model_type": "random_forest",
        "params": {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "class_weight": class_weight
        },
        "feature_importance": feature_importance,
        **train_results,
        **test_results
    }
    
    logger.info(f"✅ Modello Random Forest addestrato ({n_estimators} alberi)")
    return model, results


def calculate_metrics(y_true: pd.Series, y_pred: np.ndarray, prefix: str = "") -> Dict:
    """
    Calcola varie metriche di classificazione.
    
    Args:
        y_true: Target vero (0 o 1)
        y_pred: Predizioni (probabilità 0-1)
        prefix: Prefisso per i nomi delle metriche
    
    Returns:
        Dizionario con tutte le metriche
    """
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score, f1_score,
        roc_auc_score, average_precision_score, confusion_matrix,
        classification_report
    )
    
    # Check for valid inputs
    if len(y_true) == 0:
        logger.warning("y_true vuoto, metriche non calcolabili")
        return {}
    
    if len(y_pred) == 0:
        logger.warning("y_pred vuoto, metriche non calcolabili")
        return {}
    
    if len(y_true) != len(y_pred):
        logger.warning(f"Dimensione mismatch: y_true={len(y_true)}, y_pred={len(y_pred)}")
        return {}
    
    # Converte predizioni in classi (soglia 0.5)
    y_pred_class = (y_pred >= 0.5).astype(int)
    
    # Matrice di confusione
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred_class).ravel()
    
    # Calculate metrics safely
    try:
        accuracy = accuracy_score(y_true, y_pred_class)
    except:
        accuracy = 0.0
    
    try:
        precision = precision_score(y_true, y_pred_class, zero_division=0)
    except:
        precision = 0.0
    
    try:
        recall = recall_score(y_true, y_pred_class, zero_division=0)
    except:
        recall = 0.0
    
    try:
        f1 = f1_score(y_true, y_pred_class, zero_division=0)
    except:
        f1 = 0.0
    
    try:
        roc_auc = roc_auc_score(y_true, y_pred) if len(np.unique(y_true)) > 1 else 0.5
    except:
        roc_auc = 0.5
    
    try:
        avg_precision = average_precision_score(y_true, y_pred) if len(np.unique(y_true)) > 1 else 0.5
    except:
        avg_precision = 0.5
    
    try:
        class_report = classification_report(y_true, y_pred_class, output_dict=True)
    except:
        class_report = {}
    
    metrics = {
        f"{prefix}accuracy": accuracy,
        f"{prefix}precision": precision,
        f"{prefix}recall": recall,
        f"{prefix}f1_score": f1,
        f"{prefix}roc_auc": roc_auc,
        f"{prefix}average_precision": avg_precision,
        f"{prefix}confusion_matrix": {
            "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)
        },
        f"{prefix}classification_report": class_report
    }
    
    return metrics


def find_optimal_threshold(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
    n_thresholds: int = 100
) -> Tuple[float, Dict]:
    """
    Trova la soglia ottimale che massimizza l'F1-score.
    
    Args:
        model: Modello addestrato
        X: Feature matrix
        y: Target vector
        n_thresholds: Numero di soglie da testare
    
    Returns:
        best_threshold: Soglia ottimale
        results: Metriche per ogni soglia
    
    Raises:
        DataValidationError: Se i dati sono vuoti
    """
    logger.info("⚙️  Ricerca soglia ottimale...")
    
    # Check for empty data
    if len(X) == 0 or len(y) == 0:
        raise DataValidationError("Dati vuoti per ricerca soglia")
    
    # Predici probabilità
    try:
        if hasattr(model, "predict_proba"):
            y_pred = model.predict_proba(X)[:, 1]
        else:
            # Per XGBoost
            import xgboost as xgb
            dmatrix = xgb.DMatrix(X)
            y_pred = model.predict(dmatrix)
    except Exception as e:
        logger.error(f"Errore predizione: {e}")
        raise
    
    # Genera soglie da testare
    thresholds = np.linspace(0, 1, n_thresholds)
    
    best_threshold = 0.5
    best_f1 = 0
    threshold_results = []
    
    for threshold in thresholds:
        y_pred_class = (y_pred >= threshold).astype(int)
        
        # Calcola F1-score
        from sklearn.metrics import f1_score, precision_score, recall_score
        f1 = f1_score(y, y_pred_class, zero_division=0)
        precision = precision_score(y, y_pred_class, zero_division=0)
        recall = recall_score(y, y_pred_class, zero_division=0)
        
        threshold_results.append({
            "threshold": float(threshold),
            "f1_score": float(f1),
            "precision": float(precision),
            "recall": float(recall)
        })
        
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = threshold
    
    logger.info(f"✅ Soglia ottimale: {best_threshold:.3f} (F1-score: {best_f1:.3f})")
    
    return best_threshold, threshold_results


def save_model(
    model: Any,
    model_dir: Path = Path("mobile/models"),
    model_name: str = "modello_rischio",
    metadata: Optional[Dict] = None
) -> Path:
    """
    Salva il modello e i metadati su disco.
    
    Args:
        model: Modello addestrato
        model_dir: Cartella di salvataggio
        model_name: Nome base del modello
        metadata: Metadati aggiuntivi (es. parametri, metriche)
    
    Returns:
        Path al file salvato
    
    Raises:
        Exception: Se c'e' un errore nel salvataggio
    """
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Errore creazione directory modelli: {e}")
        raise
    
    # Salva modello
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = model_dir / f"{model_name}_{timestamp}.pkl"
    
    # Salvataggio tramite joblib
    joblib.dump(model, model_path)
    logger.info(f"✅ Modello salvato in: {model_path}")
    
    # Salvataggio metadati (se presenti)
    if metadata:
        meta_path = model_dir / f"{model_name}_{timestamp}_meta.json"
        with open(meta_path, 'w', encoding='utf-8') as f:
            # default=str gestisce conversioni fallback di numpy float/int che JSON fatica a serializzare
            json.dump(metadata, f, indent=4, default=str)
        logger.info(f"✅ Metadati salvati in: {meta_path}")
        
    return model_path
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", "--dataset", required=True, help="Dataset CSV di input")
    parser.add_argument("--model-type", default="xgboost", help="Tipo di modello (xgboost, random_forest)")
    parser.add_argument("--output-dir", "--model-output", default="mobile/models", help="Cartella di output per il modello")
    parser.add_argument("--final-train", action="store_true", help="Addestra sul 100% dei dati")

    # Parametri avanzati per compatibilità
    parser.add_argument("--epochs", type=int, default=1000, help="Numero di epoche (solo per XGBoost)")
    parser.add_argument("--batch-size", type=int, default=32, help="Dimensione batch (mantenuto per compatibilità)")
    parser.add_argument("--learning-rate", type=float, default=0.01, help="Tasso di apprendimento (mantenuto per compatibilità)")
    parser.add_argument("--early-stopping", type=int, default=10, help="Early stopping rounds (solo per XGBoost)")
    parser.add_argument("--validate", action="store_true", help="Esegue validazione incrociata")
    parser.add_argument("--test-size", type=float, default=0.2, help="Dimensione test set")

    args = parser.parse_args()

    # Carica
    df = load_data(args.input_csv)
    
    if args.final_train:
        logger.info("🚀 Modalità FINAL TRAIN: addestramento sul 100% dei dati")
        X_train, y_train = prepare_features(df)
        X_train, y_train = apply_smote(X_train, y_train)
        X_test, y_test = None, None
    else:
        train, test = split_data_temporal(df, test_size=args.test_size)
        X_train, y_train = prepare_features(train)
        X_test, y_test = prepare_features(test)
        X_train, y_train = apply_smote(X_train, y_train)

    # Train
    if args.model_type == "xgboost":
        model, results = train_xgboost(X_train, y_train, X_test, y_test)
    else:
        model, results = train_random_forest(X_train, y_train, X_test, y_test)

    # Salva
    save_model(model, Path(args.output_dir), metadata=results)


