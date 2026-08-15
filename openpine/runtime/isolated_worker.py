from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_IMPORTS = frozenset(
    {
        "socket",
        "subprocess",
        "ctypes",
        "multiprocessing",
        "pickle",
        "pathlib",
        "http",
        "urllib",
        "requests",
    }
)
FORBIDDEN_NAMES = frozenset({"exec", "eval", "compile", "__import__", "open"})


class IsolatedWorkerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IsolatedAdmission:
    path: str
    imports: tuple[str, ...]
    forbidden: tuple[str, ...]


def scan_generated_source(path: Path) -> IsolatedAdmission:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imports: list[str] = []
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                imports.append(alias.name)
                if root in FORBIDDEN_IMPORTS:
                    forbidden.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            imports.append(node.module)
            if root in FORBIDDEN_IMPORTS:
                forbidden.append(node.module)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            forbidden.append(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_NAMES:
                forbidden.append(node.func.id)
    return IsolatedAdmission(path=str(path), imports=tuple(imports), forbidden=tuple(dict.fromkeys(forbidden)))


def admit_generated_source(path: Path) -> IsolatedAdmission:
    admission = scan_generated_source(path)
    if admission.forbidden:
        raise IsolatedWorkerError(
            f"generated artifact failed isolated admission: {', '.join(admission.forbidden)}"
        )
    return admission


def generated_execution_mode() -> str:
    return os.environ.get("OPENPINE_GENERATED_EXECUTION", "in_process")


def require_isolated_or_scan(path: Path) -> IsolatedAdmission:
    admission = admit_generated_source(path)
    if generated_execution_mode() == "isolated":
        # Process isolation is required for 5.0 production. This admission
        # gate is the first control; the worker process is the next step.
        return admission
    return admission


_WORKER_SNIPPET = r'''
import ast, json, sys
from pathlib import Path
path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(path))
classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
print(json.dumps({"ok": True, "classes": classes}))
'''


def inspect_generated_in_process(path: Path, *, timeout_sec: float = 5.0) -> dict[str, object]:
    import subprocess
    import sys

    admit_generated_source(path)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _WORKER_SNIPPET, str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env={
            "PYTHONHASHSEED": "0",
            "OPENPINE_WORKER": "1",
            "PATH": "",
        },
    )
    if completed.returncode != 0:
        raise IsolatedWorkerError(completed.stderr.strip() or "isolated worker failed")
    import json

    payload = json.loads(completed.stdout)
    if payload.get("ok") is not True:
        raise IsolatedWorkerError("isolated worker returned unsuccessful payload")
    return payload
