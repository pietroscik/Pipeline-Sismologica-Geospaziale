"""Mobile analysis package.

Contains ML-based seismic risk analysis and alert system.
"""

from pathlib import Path

# Project root: salire di un livello dalla cartella 'mobile'
PROJECT_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["PROJECT_ROOT"]
