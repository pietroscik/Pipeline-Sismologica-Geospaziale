import os
from pathlib import Path
from path_utils import PROJECT_ROOT, get_project_root, resolve_project_path, is_csv_file, validate_csv_file

# Definisce la root del progetto (salendo di un livello dalla cartella 'mobile')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
