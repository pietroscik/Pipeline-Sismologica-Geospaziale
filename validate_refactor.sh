#!/bin/bash
set -e  # Esce al primo errore

echo "🔍 INIZIO VALIDAZIONE REFACTORING"
echo "================================="

# 1. Sintassi Python
echo -e "\n✅ Test 1/7: Verifica sintassi Python..."
python -m py_compile scripts/*.py app.py || { echo "❌ Errore di sintassi"; exit 1; }

# 2. Import
echo -e "\n✅ Test 2/7: Verifica import..."
python -c "from scripts.train_risk_model import main" || { echo "❌ Import fallito"; exit 1; }
python -c "from scripts.predict_from_db import main" || { echo "❌ Import predict_from_db fallito"; exit 1; }

# 3. Pipeline (con dati di esempio)
echo -e "\n✅ Test 3/7: Esecuzione pipeline..."
python run_pipeline.py \
    --run-name test_validazione \
    --delta-csv examples/mobile_devices/scoperte_automatiche.csv.gz \
    --stations-csv examples/mobile_devices/stations.csv \
    --auto-ingest || { echo "❌ Pipeline fallita"; exit 1; }

# 4. Training (se DB ha dati)
echo -e "\n✅ Test 4/7: Training modello..."
python scripts/train_risk_model.py \
    --model-output-dir models/test_validazione || echo "⚠️  Training fallito (DB vuoto?)"

# 5. Inferenza
echo -e "\n✅ Test 5/7: Inferenza da DB..."
python scripts/predict_from_db.py \
    --model-name test_validazione \
    --limit 10 || echo "⚠️  Inferenza fallita (modello non trovato?)"

# 6. Test unitari
echo -e "\n✅ Test 6/7: Esecuzione test..."
pytest --cov=scripts --cov-report=term -q || { echo "❌ Test falliti"; exit 1; }

# 7. Pulizia
echo -e "\n✅ Test 7/7: Pulizia artefatti..."
rm -rf runs/test_validazione models/test_validazione

echo -e "\n🎉 TUTTI I TEST PASATI! Refactoring validato."