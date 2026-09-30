"""Raw installed origins for `python -I -m ...installed_probe` outside checkout.

API/CLI semantic obligations are separate frozen-policy commands, not a flag
set by this origin probe. No acceptance verdict is produced here.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

from openpine.verification.execution_identity import hash_file

MODULES = {
    "openpine": "openpine",
    "openpine-contracts": "openpine_contracts",
    "pine2ast": "pine2ast",
    "ast2python": "ast2python",
    "pinelib": "pinelib",
    "backtest_engine": "backtest_engine",
    "marketdata-provider": "marketdata_provider",
    "optimizer": "optimizer",
}


def probe() -> dict:
    prefix = Path(sys.prefix).resolve()
    components = {}
    for component, module_name in MODULES.items():
        module = importlib.import_module(module_name)
        distribution = importlib.metadata.distribution(component.replace("_", "-"))
        direct = distribution.read_text("direct_url.json")
        editable = bool(
            direct and json.loads(direct).get("dir_info", {}).get("editable")
        )
        files = {}
        for entry in distribution.files or []:
            # Installed bytecode, scripts and metadata RECORD are derived inputs.
            if entry.parts[0] != module_name or "__pycache__" in entry.parts:
                continue
            path = Path(str(distribution.locate_file(entry))).resolve(strict=True)
            if not path.is_relative_to(prefix):
                raise ValueError("distribution escapes installed prefix")
            files[path.relative_to(prefix).as_posix()] = hash_file(path)
        if module.__file__ is None:
            raise ValueError("installed module has no file origin")
        components[component] = {
            "origin": str(Path(module.__file__).resolve(strict=True)),
            "version": distribution.version,
            "editable": editable,
            "files": files,
        }
    return {
        "schema_id": "openpine.installed_origins.v1",
        "python": platform.python_version(),
        "executable_sha256": hash_file(Path(sys.executable).resolve(strict=True)),
        "isolated": bool(sys.flags.isolated),
        "pythonpath": os.environ.get("PYTHONPATH"),
        "cwd": str(Path.cwd().resolve()),
        "prefix": str(prefix),
        "components": components,
    }


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True))
