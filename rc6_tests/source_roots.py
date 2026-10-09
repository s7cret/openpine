"""Actual imported source roots for portable native test producers."""
from importlib import import_module
from pathlib import Path
from openpine.verification.architecture import COMPONENTS

def imported_source_roots():
    return {name: Path(import_module(row[0]).__file__).resolve().parents[1]
            for name, row in COMPONENTS.items()}
