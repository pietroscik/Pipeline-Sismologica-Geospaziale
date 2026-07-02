import json
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from imblearn.over_sampling import SMOTE

# Aggiungiamo la root del progetto e la cartella mobile al path per risolvere le dipendenze

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(PROJECT_ROOT))

sys.path.insert(0, str(PROJECT_ROOT / "mobile"))

from mobile.alert_system import AlertSystem, get_alert_system
from mobile.data_validator import (DataValidationError, validate_csv_file,
                            validate_data)
from logging_config import setup_logging

# Configura logging

try:

    logger = setup_logging(__name__)

except Exception:

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger(__name__)

logger.setLevel(logging.INFO)


# Costanti

DEFAULT_MODEL_TYPE = "xgboost"

DEFAULT_MIN_STATIONS = 18

DEFAULT_ALERT_THRESHOLD = 0.7


def calculate_rolling_b_value(series: pd.Series, window_size: int) -> pd.Series:
    """
    Calcola il b-value su una finestra mobile (rolling).

    Args:
        series: Serie Pandas di "magnitudo" (proxy, es. numero di stazioni).
        window_size: Dimensione della finestra per il calcolo (es. 24 per 24 ore).

    Returns:
        Serie Pandas con il b-value calcolato per ogni punto.
    """
    b_values = []
    # Usiamo il metodo di Aki-Utsu: b = log10(e) / (mean_mag - min_mag_completeness)
    # Semplifichiamo assumendo che la magnitudo minima di completezza sia vicina alla minima osservata.
    min_mag_completeness = series[series > 0].min() if not series[series > 0].empty else 0.1

    rolling_windows = series.rolling(window=window_size, min_periods=1)

    for window in rolling_windows:
        if window.mean() > min_mag_completeness:
            # Evita divisione per zero
            denominator = window.mean() - min_mag_completeness
            if denominator > 0:
                b_value = np.log10(np.e) / denominator
                b_values.append(b_value)
            else:
                b_values.append(np.nan)
        else:
            b_values.append(np.nan)

    return pd.Series(b_values, index=series.index).fillna(method='ffill').fillna(0)

def create_ml_features_from_deltas(
    df_deltas: pd.DataFrame,
    target_window_hours: int = 24,
    target_threshold: float = 15.0,
) -> pd.DataFrame:
    """
    Trasforma un DataFrame di delta grezzi in un dataset di feature per ML.

    Questa funzione replica e migliora la logica precedentemente in `prepara_ml.py`.
    Prende i dati grezzi dei rilevamenti e li aggrega in una griglia oraria,
    calcolando feature temporali e il target per la predizione.

    Args:
        df_deltas: DataFrame con i rilevamenti (richiede 'arrival_iso', 'delta_seconds').
        target_window_hours: Finestra temporale (in ore) per cercare eventi futuri per il target.
        target_threshold: Soglia di 'energia' per definire un evento significativo.

    Returns:
        DataFrame con feature orarie pronto per il training.
    """
    logger.info("🛠️  Inizio feature engineering dal dataset di delta grezzi...")

    # 1. Validazione e preparazione iniziale
    if "arrival_iso" not in df_deltas.columns:
        raise ValueError("La colonna 'arrival_iso' è richiesta per l'analisi temporale.")
    df_deltas["arrival_iso"] = pd.to_datetime(df_deltas["arrival_iso"], errors="coerce")
    df = df_deltas.dropna(subset=["arrival_iso"]).set_index("arrival_iso").sort_index()

    # Usiamo il delta_seconds come proxy dell'energia (ritardo = anomalia)
    df["energia"] = pd.to_numeric(df["delta_seconds"], errors="coerce").fillna(0)

    # 2. Raggruppamento in griglia oraria continua
    logger.info("   - Aggregazione dati su base oraria...")
    # Assicuriamoci che ci sia un range di date per creare la griglia
    if len(df.index) > 1:
        full_range = pd.date_range(start=df.index.min().floor('H'), end=df.index.max().ceil('H'), freq='H')
        df_hourly = pd.DataFrame(index=full_range)
    else:
        df_hourly = pd.DataFrame(index=df.index.floor('H'))

    # Calcolo delle feature aggregate
    hourly_agg = df.resample("1H").agg(
        numero_eventi=("energia", "size"),
        energia_max=("energia", "max"),
        energia_media=("energia", "mean"),
        energia_std=("energia", "std"),
    ).fillna(0)

    df_hourly = df_hourly.join(hourly_agg, how='left').fillna(0)

    # 3. Calcolo Finestre Mobili (Rolling Features)
    logger.info("   - Calcolo feature basate su finestre mobili (6h, 12h, 24h)...")
    for window in [6, 12, 24, 48]:
        df_hourly[f"eventi_ultime_{window}h"] = df_hourly["numero_eventi"].rolling(window, min_periods=1).sum()
        df_hourly[f"energia_max_ultime_{window}h"] = df_hourly["energia_max"].rolling(window, min_periods=1).max()
        df_hourly[f"energia_media_ultime_{window}h"] = df_hourly["energia_media"].rolling(window, min_periods=1).mean()

    # 4. Feature Temporali (ora del giorno, giorno della settimana, etc.)
    logger.info("   - Aggiunta feature cicliche temporali...")
    df_hourly["ora_del_giorno"] = df_hourly.index.hour
    df_hourly["giorno_della_settimana"] = df_hourly.index.dayofweek
    df_hourly["is_notte"] = ((df_hourly["ora_del_giorno"] >= 22) | (df_hourly["ora_del_giorno"] <= 6)).astype(int)
    df_hourly["is_weekend"] = (df_hourly["giorno_della_settimana"] >= 5).astype(int)

    # 5. Calcolo b-value su finestra mobile (Feature avanzata)
    logger.info("   - Calcolo b-value su finestra mobile (24h)...")
    # Usiamo 'numero_eventi' come proxy della magnitudo per il calcolo del b-value
    df_hourly['bvalue_rolling_24h'] = calculate_rolling_b_value(df_hourly['numero_eventi'], window_size=24)

    # 5. Creazione del Target per l'addestramento
    logger.info(f"   - Creazione del target 'Target_Allarme' (finestra: {target_window_hours}h, soglia energia: >{target_threshold})...")
    # Calcola l'energia massima nella finestra futura per ogni punto temporale
    df_hourly['max_energia_futura'] = df_hourly['energia_max'].shift(-target_window_hours).rolling(window=target_window_hours, min_periods=1).max()

    # Il target è 1 se l'energia massima futura supera la soglia
    df_hourly["Target_Allarme"] = (df_hourly["max_energia_futura"] > target_threshold).astype(int)

    # Rimuovi le ultime righe dove il target non può essere calcolato
    df_hourly = df_hourly.iloc[:-target_window_hours]

    logger.info(f"✅ Feature engineering completato. Creato dataset di {len(df_hourly)} campioni orari.")
    
    # Riorganizza le colonne per coerenza
    feature_cols = [
        'numero_eventi', 'energia_max', 'energia_media', 'energia_std',
        'eventi_ultime_6h', 'energia_max_ultime_6h', 'energia_media_ultime_6h',
        'eventi_ultime_12h', 'energia_max_ultime_12h', 'energia_media_ultime_12h',
        'eventi_ultime_24h', 'energia_max_ultime_24h', 'energia_media_ultime_24h',
        'eventi_ultime_48h', 'energia_max_ultime_48h', 'energia_media_ultime_48h',
        'ora_del_giorno', 'giorno_della_settimana', 'is_notte', 'is_weekend'
    ] + ['bvalue_rolling_24h'] # Aggiunta del b-value alle feature

    target_col = 'Target_Allarme'
    
    # Assicurati che tutte le colonne esistano, riempiendo con 0 se mancano
    for col in feature_cols + [target_col]:
        if col not in df_hourly.columns:
            df_hourly[col] = 0
            
    return df_hourly[feature_cols + [target_col]]


def load_data(dataset_path: Path) -> pd.DataFrame:
    """Carica il dataset per il training."""

    logger.info(f"📖 Caricamento dataset da {dataset_path}...")

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset file non trovato: {dataset_path}")
    if dataset_path.stat().st_size == 0:
        raise DataValidationError(
            "Dataset file e' vuoto", errors=["Dataset file is empty"]
        )

    # Load and validate CSV

    # MODIFICA: Controlliamo se il file è già un dataset ML o se è un file di delta grezzi
    df_raw = pd.read_csv(dataset_path)
    
    # Se mancano le colonne tipiche del ML, ma ci sono quelle dei delta,
    # allora eseguiamo il feature engineering.
    if "Target_Allarme" not in df_raw.columns and "arrival_iso" in df_raw.columns:
        logger.info("Rilevato dataset di delta grezzi. Avvio del feature engineering...")
        df = create_ml_features_from_deltas(df_raw)
    else:
        logger.info("Rilevato dataset ML pre-elaborato.")
        try:
            df = validate_csv_file(dataset_path, required_columns={"Target_Allarme"})
        except DataValidationError as e:
            logger.error(f"❌ Validazione dataset ML fallita: {e.message}")
            for err in e.errors:
                logger.error(f"   {err}")
            raise

    # Convert datetime index

    if "Tempo" in df.columns:

        df["Tempo"] = pd.to_datetime(df["Tempo"])

        df.set_index("Tempo", inplace=True)

    elif df.index.name is None or df.index.name == "Unnamed: 0":

        # Try to find datetime column

        datetime_cols = [
            col for col in df.columns if "time" in col.lower() or "tempo" in col.lower()
        ]

        if datetime_cols:

            df[datetime_cols[0]] = pd.to_datetime(df[datetime_cols[0]])

            df.set_index(datetime_cols[0], inplace=True)

        else:

            logger.warning("Nessuna colonna temporale trovata, uso indice numerico")

    # Sort by index

    df.sort_index(inplace=True)

    # Check for empty DataFrame

    if len(df) == 0:

        raise DataValidationError(
            "Dataset e' vuoto", errors=["Dataset contains no rows"]
        )

    # Check for valid target values

    if "Target_Allarme" not in df.columns:

        raise DataValidationError(
            "Colonna Target_Allarme non trovata", errors=["Target column missing"]
        )

    # Check class balance

    target_counts = df["Target_Allarme"].value_counts()

    logger.info(f"✅ Caricati {len(df)} record con {len(df.columns)} feature")

    logger.info(f"   Distribuzione target: {dict(target_counts)}")

    # Warn if dataset is too small

    if len(df) < 100:

        logger.warning(
            f"Dataset molto piccolo ({len(df)} record), risultati potrebbero non essere affidabili"
        )

    return df


def split_data_temporal(
    df: pd.DataFrame, test_size: float = 0.2, random_state: int = 42
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """

    Suddivisione temporale dei dati (non casuale per mantenere l'ordine temporale).

    Args:

        df: DataFrame con indice temporale

        test_size: Percentuale di dati per il test (0-1)

        random_state: Seed per riproducibilità (non usato in split temporale)

    Returns:

        train, test: DataFrame di training e test

    Raises:

        DataValidationError: Se il dataset e' troppo piccolo

    """

    if len(df) == 0:

        raise DataValidationError(
            "Dataset vuoto, impossibile suddividere", errors=["Empty dataset"]
        )

    # Check test_size is valid

    if not 0 < test_size < 1:

        raise ValueError(
            f"test_size deve essere compreso tra 0 e 1, ricevuto: {test_size}"
        )

    # Calcola il punto di split

    split_idx = int(len(df) * (1 - test_size))

    if split_idx == 0:

        raise DataValidationError(
            "Dataset troppo piccolo per suddivisione",
            errors=[
                f"Dataset has only {len(df)} rows, need at least {int(1/test_size)}"
            ],
        )

    # Controllo bilanciamento classi per serie temporali fortemente sbilanciate

    target_col = "Target_Allarme"

    if target_col in df.columns:

        if df[target_col].iloc[:split_idx].nunique() < 2:

            logger.warning(
                "⚠️ Suddivisione temporale sbilanciata: il train set avrebbe una sola classe."
            )

            minority_class = df[target_col].value_counts().idxmin()

            minority_positions = np.where(df[target_col].values == minority_class)[0]

            if len(minority_positions) > 1:

                # Sposta lo split a metà delle occorrenze della classe di minoranza

                split_idx = minority_positions[len(minority_positions) // 2]

                logger.info(
                    f"🔄 Aggiustamento automatico dello split per includere anomalie nel training (nuovo test_size: {1 - split_idx/len(df):.2f})"
                )

    train = df.iloc[:split_idx].copy()

    test = df.iloc[split_idx:].copy()

    # Validate splits

    if len(train) == 0:

        raise DataValidationError("Train set vuoto dopo split")

    if len(test) == 0:

        raise DataValidationError("Test set vuoto dopo split")

    logger.info(f"📚 Train: {len(train)} record, Test: {len(test)} record")

    return train, test


def prepare_features(
    df: pd.DataFrame, target_column: str = "Target_Allarme", drop_columns: list = None
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
            errors=[f"Target column '{target_column}' not found in DataFrame"],
        )

    # Aggiungi colonne da escludere

    exclude = drop_columns + [target_column]

    # Seleziona feature

    feature_columns = [col for col in df.columns if col not in exclude]

    if len(feature_columns) == 0:

        raise DataValidationError(
            "Nessuna feature disponibile dopo esclusione colonne",
            errors=["No features available after excluding target and drop columns"],
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
    random_state: int = 42,
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
            errors=[f"Target has only one class: {y_train.unique()}"],
        )

    # Converte in DMatrix (formato ottimizzato per XGBoost)

    dtrain = xgb.DMatrix(X_train, label=y_train)

    # Parametri del modello

    params = {
        "objective": "binary:logistic",
        "eval_metric": eval_metric,
        "scale_pos_weight": (
            len(y_train[y_train == 0]) / len(y_train[y_train == 1])
            if len(y_train[y_train == 1]) > 0
            else 1
        ),
        "seed": random_state,
        "verbosity": 0,
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
        verbose_eval=50,
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
        **test_results,
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
    random_state: int = 42,
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

        logger.error(
            "scikit-learn non installato. Installa con: pip install scikit-learn"
        )

        raise ImportError("scikit-learn library not installed")

    logger.info("🌳 Addestramento modello Random Forest...")

    # Check for empty data

    if len(X_train) == 0 or len(y_train) == 0:

        raise DataValidationError("Dati di training vuoti")

    # Check for single class

    if len(y_train.unique()) < 2:

        raise DataValidationError(
            "Solo una classe nel target",
            errors=[f"Target has only one class: {y_train.unique()}"],
        )

    model = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        class_weight=class_weight,
        random_state=random_state,
        n_jobs=-1,
        verbose=0,
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
            "class_weight": class_weight,
        },
        "feature_importance": feature_importance,
        **train_results,
        **test_results,
    }

    logger.info(f"✅ Modello Random Forest addestrato ({n_estimators} alberi)")

    return model, results


def train_gnn_spatiotemporal(
    df_features: pd.DataFrame,
    stations_csv: Path,
    target_column: str = "Target_Allarme",
    epochs: int = 100,
    learning_rate: float = 0.01,
) -> Tuple[Any, Dict]:
    """

    [SPERIMENTALE] Addestra una Graph Neural Network (GNN) per dati spaziali sismologici.

    Richiede: pip install torch torch_geometric

    Args:

        df_features: DataFrame con le feature temporali

        stations_csv: Path al CSV con le coordinate delle stazioni per creare il grafo

        target_column: Colonna da predire

    """

    try:

        import torch
        import torch.nn.functional as F
        from torch_geometric.data import Data
        from torch_geometric.nn import GCNConv

    except ImportError:

        logger.error(
            "❌ Librerie GNN non trovate. Installa con: pip install torch torch_geometric"
        )

        logger.info(
            "ℹ️ Il modello GNN richiede PyTorch e PyTorch Geometric per elaborare la rete spaziale."
        )

        return None, {}

    logger.info("🕸️ Preparazione Grafo Spaziale per GNN...")

    # 1. COSTRUZIONE MATRICE DI ADIACENZA (ARCHI DEL GRAFO)

    # Calcoliamo le distanze tra tutte le stazioni per creare i collegamenti (Edges)

    df_stations = pd.read_csv(stations_csv)

    edges_source = []

    edges_target = []

    edge_weights = []

    # Creiamo un dizionario per mappare il nome stazione a un ID numerico del nodo

    station_to_id = {row["station"]: idx for idx, row in df_stations.iterrows()}

    # Creiamo gli archi basandoci sulla vicinanza spaziale (es. raggio di 10km)

    from geopy.distance import geodesic

    for i, row1 in df_stations.iterrows():

        for j, row2 in df_stations.iterrows():

            if i != j:

                dist_km = geodesic(
                    (row1["latitude"], row1["longitude"]),
                    (row2["latitude"], row2["longitude"]),
                ).kilometers

                if dist_km < 10.0:  # Collega stazioni vicine

                    edges_source.append(i)

                    edges_target.append(j)

                    edge_weights.append(
                        1.0 / (dist_km + 0.1)
                    )  # Peso inversamente prop. alla distanza

    edge_index = torch.tensor([edges_source, edges_target], dtype=torch.long)

    edge_attr = torch.tensor(edge_weights, dtype=torch.float)

    # ==========================================

    # ARCHITETTURA MODELLO GRAFO (GraphSAGE / GCN)

    # ==========================================

    class SeismicGNN(torch.nn.Module):

        def __init__(self, num_node_features):

            super(SeismicGNN, self).__init__()

            # Prima convoluzione spaziale: aggrega le informazioni dalle stazioni vicine

            self.conv1 = GCNConv(num_node_features, 16)

            # Seconda convoluzione spaziale per pattern più a lungo raggio

            self.conv2 = GCNConv(16, 8)

            # Layer finale di classificazione

            self.classifier = torch.nn.Linear(8, 1)

        def forward(self, x, edge_index, edge_weight):

            # Passaggio 1: Aggregazione vicinato con attivazione ReLU

            x = self.conv1(x, edge_index, edge_weight)

            x = F.relu(x)

            x = F.dropout(x, p=0.2, training=self.training)

            # Passaggio 2: Aggregazione livello 2

            x = self.conv2(x, edge_index, edge_weight)

            x = F.relu(x)

            # Passaggio 3: Classificazione rischio sismico

            out = self.classifier(x)

            return torch.sigmoid(out)

    # NOTA: L'implementazione completa richiederebbe la trasformazione di df_features

    # in tensori per ogni timestamp temporale.

    logger.info(
        f"✅ Grafo Costruito: {len(station_to_id)} Nodi, {len(edges_source)} Archi."
    )

    logger.warning(
        "⚠️ L'addestramento GNN completo richiede l'integrazione col loop temporale."
    )

    # Simuliamo i risultati da restituire alla pipeline

    results = {
        "model_type": "gnn_gcn",
        "nodes": len(station_to_id),
        "edges": len(edges_source),
        "params": {"epochs": epochs, "learning_rate": learning_rate},
        "status": "architettura_inizializzata",
    }

    return SeismicGNN, results


def create_temporal_sequences(
    X: pd.DataFrame, y: pd.Series, sequence_length: int = 10
) -> Tuple[np.ndarray, np.ndarray]:
    """

    Converte dataset tabellari 2D in sequenze temporali 3D usando una finestra scorrevole.

    Args:

        X: Feature matrix (deve essere ordinata cronologicamente)

        y: Target vector

        sequence_length: Dimensione della finestra temporale (lookback)



    Returns:

        X_seq: Array 3D (samples, sequence_length, features)

        y_seq: Array 1D (samples,)

    """

    logger.info(f"🔄 Estrazione finestre temporali 3D (lookback={sequence_length})...")

    if len(X) < sequence_length:

        raise DataValidationError(
            f"Dataset troppo piccolo per sequence_length={sequence_length}"
        )

    X_array, y_array = X.values, y.values

    X_seq, y_seq = [], []

    for i in range(len(X) - sequence_length + 1):

        X_seq.append(X_array[i : i + sequence_length])

        y_seq.append(y_array[i + sequence_length - 1])

    X_np, y_np = np.array(X_seq, dtype=np.float32), np.array(y_seq, dtype=np.float32)

    logger.info(f"✅ Trasformazione completata: 2D {X.shape} -> 3D {X_np.shape}")

    return X_np, y_np


try:

    import torch
    import torch.nn as nn

    class SeismicTransformer(nn.Module):

        def __init__(self, num_features, d_model=32, nhead=4, num_layers=2):

            super(SeismicTransformer, self).__init__()

            self.embedding = nn.Linear(num_features, d_model)

            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, batch_first=True
            )

            self.transformer_encoder = nn.TransformerEncoder(
                encoder_layer, num_layers=num_layers
            )

            self.classifier = nn.Sequential(
                nn.Linear(d_model, 16),
                nn.ReLU(),
                nn.Linear(16, 1),  # Rimosso Sigmoid: usiamo BCEWithLogitsLoss
            )

        def forward(self, x):

            x = self.embedding(x)

            x = self.transformer_encoder(x)

            x = x[:, -1, :]

            return self.classifier(x)

except ImportError:

    SeismicTransformer = None


def train_temporal_transformer(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame = None,
    y_test: pd.Series = None,
    epochs: int = 50,
    learning_rate: float = 0.001,
    sequence_length: int = 10,
) -> Tuple[Any, Dict]:
    """

    [SPERIMENTALE] Addestra un modello Transformer (Self-Attention) per l'analisi

    delle sequenze temporali dei delta sismici.

    """

    try:

        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

    except ImportError:

        logger.error("❌ Libreria PyTorch non trovata. Installa con: pip install torch")

        return None, {}

    logger.info("🤖 Inizializzazione architettura Temporal Transformer...")

    # Preparazione dei dati 3D (Sliding Window)

    X_train = X_train.fillna(method="ffill").fillna(0)
    if X_test is not None:
        X_test = X_test.fillna(method="ffill").fillna(0)

    X_train_3d, y_train_1d = create_temporal_sequences(
        X_train, y_train, sequence_length
    )

    # Creazione dei tensori e del DataLoader PyTorch per il batching

    train_dataset = TensorDataset(
        torch.tensor(X_train_3d),
        torch.tensor(y_train_1d).unsqueeze(
            1
        ),  # Aggiungiamo una dimensione (batch, 1) per il layer Sigmoid
    )

    # Nota: Shuffle=True va bene qui per mescolare i batch DURANTE il training,

    # poiché l'ordine temporale ALL'INTERNO della singola finestra è già stato "congelato" nel 3D.

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)

    import torch.optim as optim

    # Istanziazione del modello con il corretto numero di feature

    model = SeismicTransformer(num_features=X_train.shape[1])

    logger.info(
        f"✅ Dati formattati e DataLoader creati. Inizio addestramento Transformer..."
    )

    # Bilanciamento pesi dinamico per lo sbilanciamento delle allerte

    num_neg = (y_train == 0).sum()

    num_pos = (y_train == 1).sum()

    pos_weight = torch.tensor([num_neg / max(num_pos, 1)], dtype=torch.float32)

    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    optimizer = optim.Adam(model.parameters(), lr=learning_rate)

    # Preparazione test set (se disponibile)

    X_test_3d, y_test_1d = None, None

    if X_test is not None and y_test is not None and len(X_test) >= sequence_length:

        X_test_3d, y_test_1d = create_temporal_sequences(
            X_test, y_test, sequence_length
        )

        X_test_tensor = torch.tensor(X_test_3d)

        y_test_tensor = torch.tensor(y_test_1d).unsqueeze(1)

    model.train()

    for epoch in range(epochs):

        epoch_loss = 0.0

        for batch_X, batch_y in train_loader:

            optimizer.zero_grad()

            outputs = model(batch_X)

            loss = criterion(outputs, batch_y)

            loss.backward()

            optimizer.step()

            epoch_loss += loss.item()

        avg_loss = epoch_loss / len(train_loader)

        if (epoch + 1) % 10 == 0 or epoch == 0:

            logger.info(f"   Epoca [{epoch+1}/{epochs}] - Train Loss: {avg_loss:.4f}")

    # Calcolo metriche finali

    logger.info("📊 Calcolo metriche finali...")

    model.eval()

    with torch.no_grad():

        # Predizioni sul train

        X_train_tensor = torch.tensor(X_train_3d)

        # Usiamo torch.sigmoid per riconvertire i logits in probabilità 0-1

        train_preds = torch.sigmoid(model(X_train_tensor)).squeeze().numpy()

        train_results = calculate_metrics(y_train_1d, train_preds)

        # Predizioni sul test

        test_results = {}

        if X_test_3d is not None:

            test_preds = torch.sigmoid(model(X_test_tensor)).squeeze().numpy()

            test_results = calculate_metrics(y_test_1d, test_preds, prefix="test_")

    results = {
        "model_type": "temporal_transformer",
        "params": {
            "epochs": epochs,
            "learning_rate": learning_rate,
            "sequence_length": sequence_length,
        },
        **train_results,
        **test_results,
    }

    logger.info("✅ Modello Transformer addestrato con successo!")

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

    from sklearn.metrics import (accuracy_score, average_precision_score,
                                 brier_score_loss, classification_report,
                                 cohen_kappa_score, confusion_matrix, f1_score,
                                 log_loss, matthews_corrcoef, precision_score,
                                 recall_score, roc_auc_score)

    # Check for valid inputs

    if len(y_true) == 0:

        logger.warning("y_true vuoto, metriche non calcolabili")

        return {}

    if len(y_pred) == 0:

        logger.warning("y_pred vuoto, metriche non calcolabili")

        return {}

    if len(y_true) != len(y_pred):

        logger.warning(
            f"Dimensione mismatch: y_true={len(y_true)}, y_pred={len(y_pred)}"
        )

        return {}

    import warnings

    from sklearn.exceptions import UndefinedMetricWarning

    warnings.filterwarnings("ignore", category=UserWarning)

    warnings.filterwarnings("ignore", category=UndefinedMetricWarning)

    warnings.filterwarnings("ignore", category=RuntimeWarning)

    # Converte predizioni in classi (soglia 0.5)

    y_pred_class = (y_pred >= 0.5).astype(int)

    # Matrice di confusione

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred_class, labels=[0, 1]).ravel()

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

        avg_precision = (
            average_precision_score(y_true, y_pred)
            if len(np.unique(y_true)) > 1
            else 0.5
        )

    except:

        avg_precision = 0.5

    try:

        class_report = classification_report(y_true, y_pred_class, output_dict=True)

    except:

        class_report = {}

    # Metriche Avanzate per Modelli Sbilanciati / Probabilistici

    try:

        brier = brier_score_loss(y_true, y_pred)

    except:

        brier = np.nan

    try:

        mcc = matthews_corrcoef(y_true, y_pred_class)

    except:

        mcc = np.nan

    try:

        kappa = cohen_kappa_score(y_true, y_pred_class)

    except:

        kappa = np.nan

    metrics = {
        f"{prefix}accuracy": accuracy,
        f"{prefix}precision": precision,
        f"{prefix}recall": recall,
        f"{prefix}f1_score": f1,
        f"{prefix}roc_auc": roc_auc,
        f"{prefix}average_precision": avg_precision,
        f"{prefix}brier_score": brier,
        f"{prefix}mcc": mcc,
        f"{prefix}cohen_kappa": kappa,
        f"{prefix}confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
        f"{prefix}classification_report": class_report,
    }

    return metrics


def compare_models_performance(models_results: Dict[str, Dict]) -> pd.DataFrame:
    """

    Confronta le metriche di più modelli e restituisce un DataFrame formattato.

    Args:

        models_results: Dizionario con struttura {nome_modello: risultati_da_train_fun}

    """

    logger.info("📊 Generazione Tabella di Confronto Modelli...")

    records = []

    for model_name, res in models_results.items():

        record = {"Modello": model_name.upper()}

        # Cerca metriche di test (prioritarie) o metriche di training come ripiego

        prefix = "test_" if any(k.startswith("test_") for k in res.keys()) else ""

        record["F1-Score"] = res.get(f"{prefix}f1_score", np.nan)

        record["ROC-AUC"] = res.get(f"{prefix}roc_auc", np.nan)

        record["PR-AUC"] = res.get(f"{prefix}average_precision", np.nan)

        record["MCC"] = res.get(f"{prefix}mcc", np.nan)

        record["Cohen Kappa"] = res.get(f"{prefix}cohen_kappa", np.nan)

        record["Brier Score (↓)"] = res.get(f"{prefix}brier_score", np.nan)

        records.append(record)

    df_compare = pd.DataFrame(records).set_index("Modello")

    # Ordiniamo in base all'F1-Score (o potresti farlo in base all'MCC)

    df_compare = df_compare.sort_values(by="F1-Score", ascending=False).round(4)

    return df_compare


def find_optimal_threshold(
    model: Any, X: pd.DataFrame, y: pd.Series, n_thresholds: int = 100
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

        threshold_results.append(
            {
                "threshold": float(threshold),
                "f1_score": float(f1),
                "precision": float(precision),
                "recall": float(recall),
            }
        )

        if f1 > best_f1:

            best_f1 = f1

            best_threshold = threshold

    logger.info(f"✅ Soglia ottimale: {best_threshold:.3f} (F1-score: {best_f1:.3f})")

    return best_threshold, threshold_results


def save_model(
    model: Any,
    model_dir: Path = Path("mobile/models"),
    model_name: str = "modello_rischio",
    metadata: Optional[Dict] = None,
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

    # Salvataggio Dinamico: Joblib per modelli classici, Torch Save per Deep Learning

    if "torch" in str(type(model)):

        import torch

        model_path = model_dir / f"{model_name}_{timestamp}.pth"

        torch.save(model.state_dict(), model_path)

        logger.info(f"✅ Modello PyTorch salvato in: {model_path}")

    else:

        joblib.dump(model, model_path)

        logger.info(f"✅ Modello Machine Learning salvato in: {model_path}")

    # Salvataggio metadati (se presenti)

    if metadata:

        meta_path = model_dir / f"{model_name}_{timestamp}_meta.json"

        with open(meta_path, "w", encoding="utf-8") as f:

            # default=str gestisce conversioni fallback di numpy float/int che JSON fatica a serializzare

            json.dump(metadata, f, indent=4, default=str)

        logger.info(f"✅ Metadati salvati in: {meta_path}")

    return model_path


def main():

    parser = argparse.ArgumentParser(
        description="Addestramento e confronto modelli ML sismici"
    )
    parser.add_argument(
        "--dataset", type=Path, required=True, help="Percorso al file CSV di input (delta grezzi o già feature-engineered)"
    )
    parser.add_argument(
        "--model-output-dir",
        type=Path,
        default=PROJECT_ROOT / "mobile" / "models",
        help="Directory di output per i modelli",
    )
    parser.add_argument(
        "--model-output-name",
        type=str,
        default="modello_rischio",
        help="Nome base per i file del modello",
    )
    parser.add_argument(
        "--model-type",
        type=str,
        default="compare",
        help="Tipo di modello (xgboost, random_forest, transformer, compare)",
    )
    parser.add_argument(
        "--epochs", type=int, default=50, help="Numero di epoche per Deep Learning"
    )
    parser.add_argument(
        "--learning-rate", type=float, default=0.001, help="Learning rate"
    )
    parser.add_argument(
        "--sequence-length",
        type=int,
        default=10,
        help="Lunghezza sequenza per Transformer",
    )
    parser.add_argument(
        "--generate-alerts",
        action="store_true",
        help="Genera allarmi se ci sono anomalie",
    )
    args = parser.parse_args()

    logger.info("🚀 Avvio del modulo di training e confronto modelli...")

    try:
        df = load_data(args.dataset)
        df_train, df_test = split_data_temporal(df, test_size=0.2)
        X_train, y_train = prepare_features(df_train)
        X_test, y_test = prepare_features(df_test)

        results_dict = {}
        best_model = None
        best_model_name = ""
        best_f1 = -1.0

        if args.model_type in ["xgboost", "compare"]:
            model_xgb, res_xgb = train_xgboost(X_train, y_train, X_test, y_test)
            if model_xgb:
                results_dict["xgboost"] = res_xgb
                f1 = res_xgb.get("test_f1_score", 0)
                if f1 > best_f1:
                    best_f1 = f1
                    best_model = model_xgb
                    best_model_name = "xgboost"

        if args.model_type in ["random_forest", "compare"]:
            model_rf, res_rf = train_random_forest(X_train, y_train, X_test, y_test)
            if model_rf:
                results_dict["random_forest"] = res_rf
                f1 = res_rf.get("test_f1_score", 0)
                if f1 > best_f1:
                    best_f1 = f1
                    best_model = model_rf
                    best_model_name = "random_forest"

        if args.model_type in ["transformer", "compare"]:
            model_tf, res_tf = train_temporal_transformer(
                X_train,
                y_train,
                X_test,
                y_test,
                epochs=args.epochs,
                learning_rate=args.learning_rate,
                sequence_length=args.sequence_length,
            )
            if model_tf:
                results_dict["transformer"] = res_tf
                f1 = res_tf.get("test_f1_score", 0)
                if f1 > best_f1:
                    best_f1 = f1
                    best_model = model_tf
                    best_model_name = "transformer"

        if results_dict:
            df_comparison = compare_models_performance(results_dict)
            logger.info("\n🏆 CLASSIFICA MODELLI:\n\n" + df_comparison.to_string())

            output_dir = args.model_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            df_comparison.to_csv(output_dir / "classifica_modelli.csv")

            if best_model is not None:
                save_model(
                    best_model,
                    model_dir=output_dir,
                    model_name=f"best_{best_model_name}_{args.model_output_name}",
                )
        else:
            logger.warning("Nessun modello è stato addestrato con successo.")

        return results_dict

    except Exception as e:
        logger.error(
            f"Errore durante l'esecuzione del modulo di training: {e}", exc_info=True
        )
        raise


if __name__ == "__main__":
    main()
