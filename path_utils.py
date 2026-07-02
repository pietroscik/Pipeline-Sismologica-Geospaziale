"""Project-wide path utilities for consistent path resolution across all modules.



This module provides a centralized way to access the project root directory,

ensuring all scripts can reliably locate files regardless of their working directory.



Usage:

    from path_utils import PROJECT_ROOT, get_project_root



    # Use PROJECT_ROOT directly

    config_path = PROJECT_ROOT / "config.yaml"



    # Or use the function

    root = get_project_root()

    data_dir = root / "data"

"""

from pathlib import Path

# Absolute path to the project root directory

PROJECT_ROOT = Path(__file__).resolve().parent


def get_project_root() -> Path:
    """Returns the absolute path to the project root directory."""

    return PROJECT_ROOT


def resolve_project_path(*parts: str) -> Path:
    """Returns the path to a file or directory within the project root."""

    return PROJECT_ROOT.joinpath(*parts)


def is_csv_file(path: str | Path) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower() == ".csv"


def validate_csv_file(path: str | Path) -> Path:
    p = Path(path)
    if not is_csv_file(p):
        raise ValueError(f"Invalid CSV file: {p}")
    return p.resolve()
