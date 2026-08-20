"""OS-isolated execution of generated artifact bytes.

Threat model: generated code is untrusted. The parent/gateway process never
imports it. The child receives already-captured bytes on stdin (no path reread).
Isolation is bubblewrap: new net/pid namespaces, read-only /usr, empty tmpfs
scratch, cleared environment. There is no in-process fallback.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openpine.runtime.cgroup import CgroupError, attach_worker_tree, prepare_worker_cgroup

BWRAP = "/usr/bin/bwrap"
SANDBOX_PYTHON = "/usr/bin/python3"
WORKER_USER = "openpine-worker"
TMPFS_BYTES = 16 * 1024 * 1024
TRUSTED_DEST = "/tmp/openpine-trusted"

# Child bootstrap is stdlib-only. Host env and host home are not visible.
_BOOTSTRAP = (
    f"import sys\nsys.path.insert(0, {TRUSTED_DEST!r})\n"
    + r"""
import ast
import json
import os
import resource
import socket

FORBIDDEN = {"socket", "subprocess", "ctypes", "multiprocessing", "pathlib"}
ALLOWED = {
    "os", "math", "json", "decimal", "datetime", "collections", "typing",
    "abc", "enum", "dataclasses", "functools", "itertools", "operator",
    "re", "copy", "numbers", "pinelib", "openpine_contracts",
    "__future__", "ast2python",
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
    caller = str((globals or {}).get("__name__") or "")
    root = name.split(".", 1)[0]
    if caller and caller != "__artifact__":
        return _REAL_IMPORT(name, globals, locals, fromlist, level)
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

def _plot_value(value):
    current = getattr(value, "_current", value)
    if current is None or isinstance(current, (bool, int, str)):
        return current
    try:
        from decimal import Decimal
        if isinstance(current, Decimal):
            return format(current, "f")
    except Exception:
        pass
    if isinstance(current, float):
        text = format(current, "f").rstrip("0").rstrip(".")
        return text or "0"
    return str(current)

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
    namespace = {"__name__": "__artifact__"}
    raw_builtins = __builtins__
    if isinstance(raw_builtins, dict):
        ns_builtins = dict(raw_builtins)
    else:
        ns_builtins = {
            name: getattr(raw_builtins, name)
            for name in dir(raw_builtins)
            if not name.startswith("_")
        }
        for name in ("__build_class__", "__name__"):
            if hasattr(raw_builtins, name):
                ns_builtins[name] = getattr(raw_builtins, name)
    ns_builtins["__import__"] = _guarded_import
    namespace["__builtins__"] = ns_builtins
    namespace["__import__"] = _guarded_import
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
    events = []
    for value in list(namespace.values()):
        tape = getattr(value, "intent_tape", None)
        raw = getattr(tape, "events", None) if tape is not None else None
        if not raw:
            continue
        for item in raw:
            events.append(_safe(dict(item)))
    bars = request.get("bars") or []
    if bars:
        cls = None
        for value in namespace.values():
            if isinstance(value, type) and callable(getattr(value, "_process_bar", None)):
                cls = value
                break
        if cls is not None:
            try:
                from pinelib.core import Bar as PineBar, PineRuntime
                from pinelib.core.types import RuntimeConfig, SymbolInfo, TimeframeInfo
                rt = PineRuntime(
                    symbol_info=SymbolInfo(tickerid="S"),
                    timeframe=TimeframeInfo(value="1m", interval_ms=60000, isminutes=True, multiplier=1),
                    config=RuntimeConfig(semantic_profile=request.get("semantic_profile")),
                )
                class _NoHtfProvider:
                    def get_bars(self, *a, **k):
                        raise RuntimeError("request.security requires confirmed HTF bars")
                stamped = request.get("htf_bars") or []
                if stamped:
                    from pinelib.request.providers import InMemoryDataProvider
                    keyed = {}
                    for item in stamped:
                        if not isinstance(item, dict) or item.get("time_close") is None:
                            raise RuntimeError("request.security requires confirmed HTF bars")
                        key = (str(item.get("symbol") or ""), str(item.get("timeframe") or ""))
                        keyed.setdefault(key, []).append(
                            PineBar(
                                time=int(item.get("time", 0)),
                                open=float(item.get("open", 0)),
                                high=float(item.get("high", 0)),
                                low=float(item.get("low", 0)),
                                close=float(item.get("close", 0)),
                                volume=float(item.get("volume") or 0),
                                time_close=int(item["time_close"]),
                            )
                        )
                    rt.data_provider = InMemoryDataProvider(keyed)
                else:
                    rt.data_provider = _NoHtfProvider()
            except Exception as exc:
                json.dump({"ok": False, "error": f"pine runtime: {exc}"}, sys.stdout)
                return 2
            try:
                inst = cls(params={}, runtime=rt)
            except TypeError:
                inst = cls()
            except Exception as exc:
                json.dump({"ok": False, "error": str(exc)}, sys.stdout)
                return 2
            events = []
            for i, raw_bar in enumerate(bars):
                try:
                    bar = PineBar(
                        time=int(raw_bar.get("time", 0)),
                        open=float(raw_bar.get("open", 0)),
                        high=float(raw_bar.get("high", 0)),
                        low=float(raw_bar.get("low", 0)),
                        close=float(raw_bar.get("close", 0)),
                        volume=float(raw_bar.get("volume") or 0),
                    )
                    rt.begin_bar(bar)
                except Exception:
                    bar = type("Bar", (), dict(raw_bar))()
                    rt.bar_index = i
                    rt.current_bar = bar
                ctx = getattr(inst, "ctx", None)
                if ctx is not None and getattr(ctx, "_runtime", None) is None:
                    ctx._runtime = rt
                try:
                    inst._process_bar(bar, i)
                except TypeError:
                    try:
                        inst._process_bar(bar)
                    except Exception as exc:
                        json.dump({"ok": False, "error": str(exc)}, sys.stdout)
                        return 2
                except Exception as exc:
                    json.dump({"ok": False, "error": str(exc)}, sys.stdout)
                    return 2
                end_bar = getattr(rt, "end_bar", None)
                if callable(end_bar):
                    try:
                        end_bar()
                    except Exception:
                        pass
            ctx = getattr(inst, "ctx", None)
            tape = getattr(ctx, "intent_tape", None)
            raw = getattr(tape, "events", None) if tape is not None else None
            if raw:
                events = [_safe(dict(item)) for item in raw]
    plots = []
    rec = locals().get("rt")
    recorder = getattr(rec, "plot_recorder", None) if rec is not None else None
    raw_plots = recorder.get_records() if recorder is not None else []
    for item in raw_plots:
        if isinstance(item, tuple) and len(item) >= 4:
            plots.append({
                "bar_time": int(item[0]),
                "bar_index": int(item[1]),
                "value": _plot_value(item[2]),
                "title": str(item[3]),
            })
            continue
        plots.append({
            "bar_time": int(getattr(item, "bar_time", 0)),
            "bar_index": int(getattr(item, "bar_index", 0) or 0),
            "value": _plot_value(getattr(item, "value", None)),
            "title": str(getattr(item, "title", "")),
        })
    json.dump({"ok": True, "namespace": public, "isolation": _isolation(), "intent_tape": events, "plots": plots, "semantic_profile": request.get("semantic_profile")}, sys.stdout)
    return 0

_REAL_IMPORT = __import__
if __name__ == "__main__":
    raise SystemExit(main())
"""
)


class IsolatedWorkerError(RuntimeError):
    """Typed failure from the isolated generated-code worker."""


_TRUSTED_STAGE: Path | None = None
_TRUSTED_NAMES = ("pinelib", "openpine_contracts", "ast2python")
_RUNTIME_ROOTS = ("/usr", "/lib", "/lib64")


def _chmod_tree(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        mode = 0o755 if path.is_dir() else 0o644
        path.chmod(mode | stat.S_IROTH | (stat.S_IXOTH if path.is_dir() else 0))


def _stage_trusted_packages() -> list[tuple[str, str]]:
    global _TRUSTED_STAGE
    dest_root = Path(TRUSTED_DEST)
    if _TRUSTED_STAGE is None:
        stage = Path(tempfile.mkdtemp(prefix="openpine-trusted-"))
        stage.chmod(0o755)
        for name in _TRUSTED_NAMES:
            spec = importlib.util.find_spec(name)
            if spec is None or not spec.origin:
                continue
            src = Path(spec.origin).resolve().parent
            target = stage / name
            shutil.copytree(src, target)
            _chmod_tree(target)
        _TRUSTED_STAGE = stage
    return [(str(_TRUSTED_STAGE), str(dest_root))]


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


def _runtime_ro_bind_args() -> list[str]:
    argv: list[str] = []
    for root in _RUNTIME_ROOTS:
        if Path(root).exists():
            argv.extend(["--ro-bind", root, root])
    return argv


def _bwrap_argv() -> list[str]:
    if not Path(BWRAP).is_file():
        raise IsolatedWorkerError("bubblewrap is required for isolated execution")
    if not Path(SANDBOX_PYTHON).is_file():
        raise IsolatedWorkerError("sandbox python is missing")
    prefix: list[str] = []
    if worker_user_available():
        prefix = ["/usr/bin/sudo", "-n", "-u", WORKER_USER, "--"]
    argv = prefix + [
        BWRAP,
        *_runtime_ro_bind_args(),
        "--size",
        str(TMPFS_BYTES),
        "--tmpfs",
        "/tmp",
    ]
    for src, dest in _stage_trusted_packages():
        argv.extend(["--ro-bind", src, dest])
    return argv + [
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
    semantic_profile: str = "",
    cgroup_dir: str | Path | None = None,
    bars: list[dict[str, Any]] | None = None,
    htf_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if len(source) > 500_000:
        raise IsolatedWorkerError("artifact source exceeds size limit")
    if stack_id != "openpine-5.0":
        raise IsolatedWorkerError("stack_id mismatch")
    if semantic_profile not in {"legacy_4x", "strict_5x"}:
        raise IsolatedWorkerError("semantic_profile required")
    if cgroup_dir is not None:
        try:
            prepare_worker_cgroup(cgroup_dir)
        except CgroupError as exc:
            raise IsolatedWorkerError(str(exc)) from exc
    payload = json.dumps(
        {
            "source": source.decode("utf-8"),
            "stack_id": stack_id,
            "semantic_profile": semantic_profile,
            "bars": bars or [],
            "htf_bars": htf_bars or [],
        }
    )
    try:
        # Immutable argv: trusted bwrap + /usr/bin/python3. No shell, no user path.
        proc = subprocess.Popen(  # noqa: S603
            _bwrap_argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise IsolatedWorkerError("worker spawn failed") from exc
    if cgroup_dir is not None:
        try:
            attach_worker_tree(cgroup_dir, proc.pid)
        except CgroupError as exc:
            proc.kill()
            proc.communicate()
            raise IsolatedWorkerError(str(exc)) from exc
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise IsolatedWorkerError("timeout") from exc
    completed_stdout = stdout or ""
    completed_stderr = stderr or ""
    if len(completed_stdout) > 1_000_000 or len(completed_stderr) > 1_000_000:
        raise IsolatedWorkerError("excessive worker output")
    if proc.returncode != 0:
        detail = completed_stdout.strip() or completed_stderr.strip() or "worker failed"
        raise IsolatedWorkerError(detail)
    try:
        result = json.loads(completed_stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedWorkerError("malformed worker output") from exc
    if not result.get("ok"):
        raise IsolatedWorkerError(str(result.get("error") or "worker rejected artifact"))
    return result
