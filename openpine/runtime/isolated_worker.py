"""OS-process isolated execution of generated artifact bytes.

The gateway/parent process must never import generated modules. The child
receives already-captured bytes on stdin (no second path read).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from typing import Any

# Child bootstrap is stdlib-only. Host env is not inherited.
_BOOTSTRAP = r"""
import ast
import json
import resource
import sys

FORBIDDEN = {"socket", "subprocess", "ctypes", "multiprocessing"}

def _denied(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN:
                    return root
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN:
                return root
    return None

def _safe(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    return None

def main() -> int:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    raw = sys.stdin.read(1_000_000)
    request = json.loads(raw)
    source = request["source"]
    tree = ast.parse(source)
    denied = _denied(tree)
    if denied:
        json.dump({"ok": False, "error": f"forbidden import: {denied}"}, sys.stdout)
        return 2
    namespace = {}
    exec(compile(tree, "<artifact>", "exec"), namespace, namespace)
    public = {
        key: _safe(value)
        for key, value in namespace.items()
        if not key.startswith("_")
    }
    json.dump({"ok": True, "namespace": public}, sys.stdout)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""


class IsolatedWorkerError(RuntimeError):
    """Typed failure from the isolated generated-code worker."""


def evaluate_artifact(
    source: bytes,
    *,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    scratch = tempfile.mkdtemp(prefix="openpine-worker-")
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": scratch,
    }
    try:
        # Fixed argv: current interpreter + isolated bootstrap. No shell, no user path.
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-I", "-c", _BOOTSTRAP],
            input=json.dumps({"source": source.decode("utf-8")}),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            cwd=scratch,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IsolatedWorkerError("timeout") from exc
    if completed.returncode != 0:
        detail = completed.stdout.strip() or completed.stderr.strip() or "worker failed"
        raise IsolatedWorkerError(detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedWorkerError("malformed worker output") from exc
    if not payload.get("ok"):
        raise IsolatedWorkerError(
            str(payload.get("error") or "worker rejected artifact")
        )
    return payload
