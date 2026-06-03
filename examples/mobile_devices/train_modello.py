import pandas as pd
import numpy as np
import time
import logging
import joblib
import json
from pathlib import Path
from typing import Tuple, Dict, Optional, Any
from datetime import datetime

# Importa sistema di allarme e configurazione
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "mobile"))
from alert_system import AlertSystem, get_alert_system
from logging_config import setup_logging

# Configura logging
logger = logging.getLogger(__name__)

# Costanti
DEFAULT_MODEL_TYPE = "xgboost"
DEFAULT_MIN_STATIONS = 18
DEFAULT_ALERT_THRESHOLD = 0.7


def load_data(dataset_path: str = "dataset_ml_sismico.csv") -> pd.DataFrame:
    """Carica il dataset per il training."""
    logger.info(f"📖 Caricamento dataset da {dataset_path}...")
    df = pd.read_csv(dataset_path, index_col='Tempo')
    df.index = pd.to_datetime(df.index)
    
    logger.info(f"✅ Caricati {len(df)} record con {len(df.columns)} feature")
    return df


def split_data_temporal(df: pd.DataFrame, test_size: float = 0.2, random_state: int = 42) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Suddivisione temporale dei dati (non casuale per mantenere l'ordine temporale).
    
    Args:
        df: DataFrame con indice temporale
        test_size: Percentuale di dati per il test (0-1)
        random_state: Seed per riproducibilità (non usato in split temporale)
    
    Returns:
        train, test: DataFrame di training e test
    """
    # Calcola il punto di split
    split_idx = int(len(df) * (1 - test_size))
    
    train = df.iloc[:split_idx].copy()
    test = df.iloc[split_idx:].copy()
    
    logger.info(f"📚 Train: {len(train)} record, Test: {len(test)} record")
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
    """
    if drop_columns is None:
        drop_columns = []
    
    # Aggiungi colonne da escludere
    exclude = drop_columns + [target_column]
    
    # Seleziona feature
    feature_columns = [col for col in df.columns if col not in exclude]
    
    X = df[feature_columns].copy()
    y = df[target_column].copy()
    
    logger.info(f"⚙️  Feature selezionate: {len(feature_columns)}")
    logger.debug(f"Feature: {feature_columns}")
    
    return X, y


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
    """
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("XGBoost non installato. Installa con: pip install xgboost")
        raise
    
    logger.info("🌲 Addestramento modello XGBoost...")
    
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
    """
    from sklearn.ensemble import RandomForestClassifier
    
    logger.info("🌳 Addestramento modello Random Forest...")
    
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
    
    # Converte predizioni in classi (soglia 0.5)
    y_pred_class = (y_pred >= 0.5).astype(int)
    
    # Matrice di confusione
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred_class).ravel()
    
    metrics = {
        f"{prefix}accuracy": accuracy_score(y_true, y_pred_class),
        f"{prefix}precision": precision_score(y_true, y_pred_class, zero_division=0),
        f"{prefix}recall": recall_score(y_true, y_pred_class, zero_division=0),
        f"{prefix}f1_score": f1_score(y_true, y_pred_class, zero_division=0),
        f"{prefix}roc_auc": roc_auc_score(y_true, y_pred) if len(np.unique(y_true)) > 1 else 0.5,
        f"{prefix}average_precision": average_precision_score(y_true, y_pred) if len(np.unique(y_true)) > 1 else 0.5,
        f"{prefix}confusion_matrix": {
            "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)
        },
        f"{prefix}classification_report": classification_report(y_true, y_pred_class, output_dict=True)
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
    """
    logger.info("⚙️  Ricerca soglia ottimale...")
    
    # Predici probabilità
    y_pred = model.predict_proba(X)[:, 1] if hasattr(model, "predict_proba") else model.predict(X)
    
    # Genera soglie da testare
    thresholds = np.linspace(0, 1, n_thresholds)
    
    best_threshold = 0.5
    best_f1 = 0
    threshold_results = []
    
    for threshold in thresholds:
        y_pred_class = (y_pred >= threshold).astype(int)
        
        # Calcola F1-score
        from sklearn.metrics import f1_score
        f1 = f1_score(y, y_pred_class, zero_division=0)
        
        threshold_results.append({
            "threshold": float(threshold),
            "f1_score": float(f1),
            "precision": float(precision_score(y, y_pred_class, zero_division=0)),
            "recall": float(recall_score(y, y_pred_class, zero_division=0))
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
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    
    # Salva modello
    model_path = model_dir / f"{model_name}.pkl"
    joblib.dump(model, model_path)
    
    # Salva metadati
    if metadata:
        metadata_path = model_dir / f"{model_name}_metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2, default=str)
        
        logger.info(f"💾 Metadati salvati in {metadata_path}")
    
    logger.info(f"💾 Modello salvato in {model_path}")
    return model_path


def load_model(model_path: Path) -> Tuple[Any, Optional[Dict]]:
    """
    Carica il modello e i metadati da disco.
    
    Args:
        model_path: Percorso al file del modello
    
    Returns:
        model: Modello caricato
        metadata: Metadati (se disponibili)
    """
    model = joblib.load(model_path)
    
    # Prova a caricare metadati
    metadata_path = model_path.parent / f"{model_path.stem}_metadata.json"
    metadata = None
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Errore caricamento metadati: {e}")
    
    logger.info(f"📂 Modello caricato da {model_path}")
    return model, metadata


def calculate_risk_index(
    model: Any,
    X: pd.DataFrame,
    scaler: Optional[Any] = None
) -> np.ndarray:
    """
    Calcola l'indice di rischio per nuovi dati.
    
    Args:
        model: Modello addestrato
        X: Nuovi dati (feature matrix)
        scaler: Scaler per normalizzazione (opzionale)
    
    Returns:
        risk_index: Array con indici di rischio (0-1)
    """
    # Applica scaler se fornito
    if scaler is not None:
        X_scaled = scaler.transform(X)
    else:
        X_scaled = X
    
    # Predici probabilità
    if hasattr(model, "predict_proba"):
        risk_index = model.predict_proba(X_scaled)[:, 1]
    else:
        # Per modelli che non hanno predict_proba (es. XGBoost DMatrix)
        dmatrix = xgb.DMatrix(X_scaled) if hasattr(model, "predict") else X_scaled
        risk_index = model.predict(dmatrix)
    
    return risk_index


def main():
    """Esecuzione principale del training del modello."""
    print("🧠 Addestramento Modello di Rischio Sismico...")
    tempo_inizio = time.time()
    
    try:
        # Setup logging
        setup_logging()
        
        # Carica configurazione
        config_path = Path(__file__).parent.parent / "mobile" / "config" / "alert_config.yaml"
        alert_system = get_alert_system(str(config_path))
        
        # Carica dati
        df = load_data()
        
        # Suddivisione temporale
        train, test = split_data_temporal(df, test_size=0.2)
        
        # Prepara feature e target
        X_train, y_train = prepare_features(train)
        X_test, y_test = prepare_features(test)
        
        # Seleziona tipo di modello dalla configurazione
        model_type = "xgboost"  # Default
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            model_type = config.get("model_config", {}).get("type", "xgboost")
        except Exception as e:
            logger.warning(f"Errore caricamento configurazione: {e}")
        
        # Addestramento modello
        if model_type == "xgboost":
            model, train_results = train_xgboost(
                X_train, y_train, X_test, y_test,
                early_stopping_rounds=10,
                eval_metric="aucpr"
            )
        elif model_type == "random_forest":
            model, train_results = train_random_forest(
                X_train, y_train, X_test, y_test,
                n_estimators=200,
                max_depth=10
            )
        else:
            logger.warning(f"Tipo modello non supportato: {model_type}, uso xgboost")
            model, train_results = train_xgboost(X_train, y_train, X_test, y_test)
        
        # Trova soglia ottimale
        best_threshold, threshold_results = find_optimal_threshold(model, X_test, y_test)
        
        # Calcola indice di rischio su test
        risk_indices = calculate_risk_index(model, X_test)
        
        # Genera allarmi per il set di test
        alert_triggered = False
        for i, (idx, risk_index) in enumerate(risk_indices.items()):
            if alert_system.check_threshold(
                risk_index=risk_index,
                threshold=best_threshold,
                min_stations=18,
                additional_info={
                    "model_type": model_type,
                    "timestamp": str(idx),
                    "data_point": i
                }
            ):
                alert_triggered = True
        
        # Calcola metriche finali
        test_metrics = calculate_metrics(y_test, risk_indices, prefix="test_")
        
        # Salva modello
        model_path = save_model(
            model,
            model_dir=Path("mobile/models"),
            model_name=f"modello_rischio_{model_type}",
            metadata={
                "model_type": model_type,
                "training_timestamp": datetime.now().isoformat(),
                "best_threshold": best_threshold,
                "train_samples": len(X_train),
                "test_samples": len(X_test),
                "features": list(X_train.columns),
                "metrics": test_metrics,
                "threshold_results": threshold_results
            }
        )
        
        # Stampa risultati
        tempo_elaborazione = time.time() - tempo_inizio
        print(f"
🎯 === RISULTATI FINALI MODELLO {model_type.upper()} ===")
        print("-" * 60)
        print(f"📚 Addestramento: {len(X_train)} sample")
        print(f"🔮 Test: {len(X_test)} sample")
        print(f"✅ Soglia ottimale: {best_threshold:.3f}")
        print("-" * 60)
        print(f"🎯 METRICHE SU TEST:")
        print(f"   Accuracy:  {test_metrics.get('test_accuracy', 0):.4f}")
        print(f"   Precision: {test_metrics.get('test_precision', 0):.4f}")
        print(f"   Recall:    {test_metrics.get('test_recall', 0):.4f}")
        print(f"   F1-score:  {test_metrics.get('test_f1_score', 0):.4f}")
        print(f"   ROC AUC:   {test_metrics.get('test_roc_auc', 0):.4f}")
        print(f"   Avg Precision: {test_metrics.get('test_average_precision', 0):.4f}")
        print("-" * 60)
        print(f"📊 Matrice di confusione:")
        cm = test_metrics.get('test_confusion_matrix', {})
        print(f"   TN: {cm.get('tn', 0)}, FP: {cm.get('fp', 0)}")
        print(f"   FN: {cm.get('fn', 0)}, TP: {cm.get('tp', 0)}")
        print("-" * 60)
        print(f"💾 Modello salvato: {model_path}")
        print(f"⏱️  Tempo impiegato: {tempo_elaborazione:.2f}s")
        
        if alert_triggered:
            print(f"
🚨 Allarmi generati durante il test!")
        else:
            print(f"
✅ Nessun allarme supera la soglia ottimale")
        
    except Exception as e:
        logger.error(f"❌ Errore critico in train_modello.py: {str(e)}", exc_info=True)
        alert_system.trigger_error_alert(e, "train_modello.py")
        raise


if __name__ == "__main__":
    main()