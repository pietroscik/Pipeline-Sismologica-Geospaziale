"""Project path utilities for mobile subpackage.

DEPRECATED: Use path_utils.py from project root instead.
This file is kept for backward compatibility only.
"""

import os
from pathlib import Path

# Definisce la root del progetto (salendo di un livello dalla cartella 'mobile')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
