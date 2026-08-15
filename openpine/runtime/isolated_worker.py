"""OS-isolated execution of generated artifact bytes.

Threat model: generated code is untrusted. The parent/gateway process never
imports it. The child receives already-captured bytes on stdin (no path reread).
Isolation is bubblewrap: new net/pid namespaces, read-only /usr, empty tmpfs
scratch, cleared environment. There is no in-process fallback.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

BWRAP = "/usr/bin/bwrap"
SANDBOX_PYTHON = "/usr/bin/python3"
WORKER_USER = "openpine-worker"
TMPFS_BYTES = 16 * 1024 * 1024

# Child bootstrap is stdlib-only. Host env and host home are not visible.
_BOOTSTRAP = r"""
import ast
import json
import os
import resource
import socket
import sys

FORBIDDEN = {"socket", "subprocess", "ctypes", "multiprocessing", "pathlib"}
ALLOWED = {
    "os", "math", "json", "decimal", "datetime", "collections", "typing",
    "abc", "enum", "dataclasses", "functools", "itertools", "operator",
    "re", "copy", "numbers", "pinelib", "openpine_contracts",
}

def _denied(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN or root not in ALLOWED:
                    return root
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN or root not in ALLOWED:
                return root
    return None

def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in FORBIDDEN or (level == 0 and root not in ALLOWED):
        raise ImportError(f"forbidden import: {root}")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)

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

def _isolation():
    network = "blocked"
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("1.1.1.1", 53))
        network = "open"
        probe.close()
    except OSError:
        network = "blocked"
    usr_writable = False
    try:
        with open("/usr/bin/.openpine-write-probe", "w") as handle:
            handle.write("x")
        usr_writable = True
    except OSError:
        usr_writable = False
    return {
        "uid": os.getuid(),
        "home_visible": os.path.isdir("/home") and bool(os.listdir("/home")),
        "usr_writable": usr_writable,
        "env": sorted(os.environ),
        "network": network,
    }

def main() -> int:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (134217728, 134217728))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
    except (ValueError, resource.error):
        pass
    raw = sys.stdin.read(1_000_000)
    request = json.loads(raw)
    if request.get("stack_id") != "openpine-5.0":
        json.dump({"ok": False, "error": "stack_id mismatch"}, sys.stdout)
        return 2
    if request.get("semantic_profile") not in {"legacy_4x", "strict_5x"}:
        json.dump({"ok": False, "error": "semantic_profile required"}, sys.stdout)
        return 2
    source = request["source"]
    tree = ast.parse(source)
    denied = _denied(tree)
    if denied:
        json.dump({"ok": False, "error": f"forbidden import: {denied}"}, sys.stdout)
        return 2
    namespace = {"__builtins__": __builtins__}
    if isinstance(__builtins__, dict):
        namespace["__builtins__"] = dict(__builtins__)
        namespace["__builtins__"]["__import__"] = _guarded_import
    else:
        namespace["__builtins__"].__import__ = _guarded_import
    try:
        exec(compile(tree, "<artifact>", "exec"), namespace, namespace)
    except ImportError as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout)
        return 2
    public = {
        key: _safe(value)
        for key, value in namespace.items()
        if not key.startswith("_")
    }
    json.dump({"ok": True, "namespace": public, "isolation": _isolation()}, sys.stdout)
    return 0

_REAL_IMPORT = __import__
if __name__ == "__main__":
    raise SystemExit(main())
"""


class IsolatedWorkerError(RuntimeError):
    """Typed failure from the isolated generated-code worker."""


def worker_user_available() -> bool:
    try:
        probe = subprocess.run(  # noqa: S603
            ["/usr/bin/sudo", "-n", "-u", WORKER_USER, "--", "/usr/bin/id", "-u"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0 and probe.stdout.strip().isdigit()


def _bwrap_argv() -> list[str]:
    if not Path(BWRAP).is_file():
        raise IsolatedWorkerError("bubblewrap is required for isolated execution")
    if not Path(SANDBOX_PYTHON).is_file():
        raise IsolatedWorkerError("sandbox python is missing")
    prefix: list[str] = []
    if worker_user_available():
        prefix = ["/usr/bin/sudo", "-n", "-u", WORKER_USER, "--"]
    return prefix + [
        BWRAP,
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--size",
        str(TMPFS_BYTES),
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "TZ",
        "UTC",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "PYTHONHASHSEED",
        "0",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--chdir",
        "/tmp",
        SANDBOX_PYTHON,
        "-I",
        "-c",
        _BOOTSTRAP,
    ]


def evaluate_artifact(
    source: bytes,
    *,
    timeout_s: float = 5.0,
    stack_id: str = "openpine-5.0",
    semantic_profile: str = "legacy_4x",
) -> dict[str, Any]:
    if len(source) > 500_000:
        raise IsolatedWorkerError("artifact source exceeds size limit")
    if stack_id != "openpine-5.0":
        raise IsolatedWorkerError("stack_id mismatch")
    if semantic_profile not in {"legacy_4x", "strict_5x"}:
        raise IsolatedWorkerError("semantic_profile required")
    try:
        # Immutable argv: trusted bwrap + /usr/bin/python3. No shell, no user path.
        completed = subprocess.run(  # noqa: S603
            _bwrap_argv(),
            input=json.dumps(
                {
                    "source": source.decode("utf-8"),
                    "stack_id": stack_id,
                    "semantic_profile": semantic_profile,
                }
            ),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IsolatedWorkerError("timeout") from exc
    if len(completed.stdout) > 1_000_000 or len(completed.stderr) > 1_000_000:
        raise IsolatedWorkerError("excessive worker output")
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
