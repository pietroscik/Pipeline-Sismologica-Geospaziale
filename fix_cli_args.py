#!/usr/bin/env python3
import re

print("🔧 Applico fix agli argomenti CLI...")

# ============================================================================
# FIX 1: prepare_science_deltas.py
# ============================================================================
print("\n1. Fix prepare_science_deltas.py...")
with open('scripts/prepare_science_deltas.py', 'r') as f:
    content = f.read()

# Aggiungi alias per --events-csv e --picks-csv
content = content.replace(
    'parser.add_argument("--events-csv",',
    'parser.add_argument("--events-csv", "--events-catalog",'
)
content = content.replace(
    'parser.add_argument("--picks-csv",',
    'parser.add_argument("--picks-csv", "--picks-catalog",'
)

# Aggiungi parametro --reference
old_ref = '''    parser.add_argument(
        "--network-filter",
        nargs="*",
        help="Lista di network ammessi (es. IV). Se omessa, usa tutti i network.",
    )
    return parser.parse_args()'''

new_ref = '''    parser.add_argument(
        "--network-filter",
        nargs="*",
        help="Lista di network ammessi (es. IV). Se omessa, usa tutti i network.",
    )
    parser.add_argument(
        "--reference",
        default="median",
        choices=["median", "mean", "first"],
        help="Metodo per calcolare il tempo di riferimento dell'evento (default: median).",
    )
    return parser.parse_args()'''

content = content.replace(old_ref, new_ref)

# Usa args.reference
content = content.replace(
    'merged.groupby("event_index")["arrival_epoch"].transform("median")',
    'merged.groupby("event_index")["arrival_epoch"].transform(args.reference)'
)

with open('scripts/prepare_science_deltas.py', 'w') as f:
    f.write(content)
print("   ✅ prepare_science_deltas.py fixato")

# ============================================================================
# FIX 2: download_cf_waveforms.py
# ============================================================================
print("\n2. Fix download_cf_waveforms.py...")
with open('scripts/download_cf_waveforms.py', 'r') as f:
    content = f.read()

# Aggiungi --config
old_config = '''    parser = argparse.ArgumentParser(
        description="Scarica waveform MiniSEED via FDSN per un elenco di stazioni.",
    )
    parser.add_argument("--network", default=fdsn_cfg.get("network", "IV"), help="Codice network FDSN.")'''

new_config = '''    parser = argparse.ArgumentParser(
        description="Scarica waveform MiniSEED via FDSN per un elenco di stazioni.",
    )
    parser.add_argument(
        "--config", type=str,
        help="File YAML con configurazione (sovrascrive i parametri CLI)."
    )
    parser.add_argument("--network", "--networks", default=fdsn_cfg.get("network", "IV"), help="Codice network FDSN.")'''

content = content.replace(old_config, new_config)

# Aggiungi alias per --start e --end
content = content.replace(
    'parser.add_argument("--start",',
    'parser.add_argument("--start", "--start-date",'
)
content = content.replace(
    'parser.add_argument("--end",',
    'parser.add_argument("--end", "--end-date",'
)

with open('scripts/download_cf_waveforms.py', 'w') as f:
    f.write(content)
print("   ✅ download_cf_waveforms.py fixato")

# ============================================================================
# FIX 3: analyze_delta_map.py
# ============================================================================
print("\n3. Fix analyze_delta_map.py...")
with open('scripts/analyze_delta_map.py', 'r') as f:
    content = f.read()

# Aggiungi --method
old_method = '''    parser.add_argument(
        "--anomaly-threshold", type=float, default=map_cfg.get("default_anomaly_threshold", 0.5),
        help="Se impostato, crea un plot evidenziando punti con |delta| >= soglia.",
    )
    parser.add_argument(
        "--epsg", type=int, default=geo_cfg.get("epsg", 32633),
        help="Sistema di proiezione metrico (es. 32633).",
    )'''

new_method = '''    parser.add_argument(
        "--anomaly-threshold", "--threshold", type=float, default=map_cfg.get("default_anomaly_threshold", 0.5),
        help="Se impostato, crea un plot evidenziando punti con |delta| >= soglia.",
    )
    parser.add_argument(
        "--method", default="cubic", choices=["linear", "cubic", "nearest"],
        help="Metodo di interpolazione per la griglia (default: cubic).",
    )
    parser.add_argument(
        "--epsg", type=int, default=geo_cfg.get("epsg", 32633),
        help="Sistema di proiezione metrico (es. 32633).",
    )'''

content = content.replace(old_method, new_method)

# Aggiungi --export-plot
old_export = '''    parser.add_argument(
        "--export-shapefile", action="store_true",
        help="Se impostato, esporta uno shapefile dei punti.",
    )'''

new_export = '''    parser.add_argument(
        "--export-shapefile", action="store_true",
        help="Se impostato, esporta uno shapefile dei punti.",
    )
    parser.add_argument(
        "--export-plot", action="store_true",
        help="Se impostato, esporta i plot (scatter e interpolato).",
    )'''

content = content.replace(old_export, new_export)

# Usa args.method
content = content.replace(
    'method="cubic"',
    'method=args.method'
)

# Usa args.threshold se --threshold è usato
content = content.replace(
    'args.anomaly_threshold is not None',
    'args.anomaly_threshold is not None or args.threshold is not None'
)

with open('scripts/analyze_delta_map.py', 'w') as f:
    f.write(content)
print("   ✅ analyze_delta_map.py fixato")

# ============================================================================
# FIX 4: train_modello.py
# ============================================================================
print("\n4. Fix train_modello.py...")
with open('examples/mobile_devices/train_modello.py', 'r') as f:
    content = f.read()

# Aggiungi alias e parametri avanzati
old_parser = '''    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--model-type", default="xgboost")
    parser.add_argument("--output-dir", default="mobile/models")
    parser.add_argument("--final-train", action="store_true")
    args = parser.parse_args()'''

new_parser = '''    parser = argparse.ArgumentParser()
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

    args = parser.parse_args()'''

content = content.replace(old_parser, new_parser)

# Usa args.test_size
content = content.replace(
    'train, test = split_data_temporal(df)',
    'train, test = split_data_temporal(df, test_size=args.test_size)'
)

# Usa args.early_stopping
content = content.replace(
    'early_stopping_rounds: int = 10,',
    'early_stopping_rounds: int = args.early_stopping,'
)

with open('examples/mobile_devices/train_modello.py', 'w') as f:
    f.write(content)
print("   ✅ train_modello.py fixato")

print("\n🎉 Tutti i fix CLI applicati!")
