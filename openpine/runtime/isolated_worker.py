"""Isolated generated-Python worker. Gateway never imports generated modules."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FORBIDDEN_IMPORTS = frozenset(
    {
        "socket",
        "subprocess",
        "ctypes",
        "multiprocessing",
        "pathlib",
        "os",
        "sys",
        "importlib",
        "shutil",
        "http",
        "urllib",
        "requests",
    }
)
FORBIDDEN_NAMES = frozenset({"system", "popen", "exec", "eval", "__import__"})


class IsolatedWorkerError(RuntimeError):
    code = "ISOLATED_WORKER_ERROR"


@dataclass(frozen=True, slots=True)
class IsolatedAdmission:
    path: str
    forbidden: tuple[str, ...]

    @property
    def admitted(self) -> bool:
        return not self.forbidden


def scan_generated_source(path: Path) -> IsolatedAdmission:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN_IMPORTS:
                    forbidden.append(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN_IMPORTS:
                forbidden.append(root)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            forbidden.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            forbidden.append(node.attr)
    return IsolatedAdmission(path=str(path), forbidden=tuple(dict.fromkeys(forbidden)))


def admit_generated_source(path: Path) -> IsolatedAdmission:
    admission = scan_generated_source(path)
    if admission.forbidden:
        raise IsolatedWorkerError(
            f"generated artifact failed isolated admission: {', '.join(admission.forbidden)}"
        )
    return admission


def generated_module_imported_in_parent() -> bool:
    return any(name.startswith("openpine_generated_") for name in sys.modules)


def run_isolated_generated(
    path: Path,
    request: Mapping[str, Any],
    *,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    admit_generated_source(path)
    env = {
        "PYTHONHASHSEED": "0",
        "OPENPINE_WORKER": "1",
        "TZ": "UTC",
        "LANG": "C",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV", ""),
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "openpine.runtime.isolated_worker", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        input=json.dumps(request, separators=(",", ":")),
        env={key: value for key, value in env.items() if value},
    )
    if completed.returncode != 0:
        raise IsolatedWorkerError(completed.stderr.strip() or "isolated worker failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedWorkerError("isolated worker returned invalid JSON") from exc
    if payload.get("ok") is not True:
        raise IsolatedWorkerError(str(payload.get("error") or "isolated worker unsuccessful"))
    return payload


class IsolatedGeneratedAdapter:
    """Parent-process proxy. Holds no generated class and never imports it."""

    def __init__(self, params: Mapping[str, Any], runtime: Any, ctx: Any) -> None:
        self.params = dict(params)
        self.runtime = runtime
        self.ctx = ctx
        self._path = Path(str(params["_isolated_artifact_path"]))

    def _process_bar(self, bar: Any, bar_index: int) -> None:
        payload = run_isolated_generated(
            self._path,
            {
                "action": "process_bar",
                "bar_index": bar_index,
                "bar": {
                    "time": getattr(bar, "time", 0),
                    "open": str(getattr(bar, "open", 0)),
                    "high": str(getattr(bar, "high", 0)),
                    "low": str(getattr(bar, "low", 0)),
                    "close": str(getattr(bar, "close", 0)),
                    "volume": str(getattr(bar, "volume", 0) or 0),
                },
            },
        )
        for event in payload.get("intents") or []:
            if hasattr(self.ctx, "intent_tape") and hasattr(self.ctx, "_record_intent"):
                self.ctx._record_intent(
                    str(event.get("kind") or "order"),
                    str(event.get("order_id") or "isolated"),
                    qty=event.get("qty"),
                    comment=event.get("comment"),
                )


def make_isolated_adapter(path: Path) -> type[IsolatedGeneratedAdapter]:
    artifact_path = path

    class BoundIsolatedGeneratedAdapter(IsolatedGeneratedAdapter):
        def __init__(self, params: Mapping[str, Any], runtime: Any, ctx: Any) -> None:
            merged = dict(params)
            merged["_isolated_artifact_path"] = str(artifact_path)
            super().__init__(merged, runtime, ctx)

    BoundIsolatedGeneratedAdapter.__name__ = "GeneratedStrategy"
    return BoundIsolatedGeneratedAdapter


def _worker_process_bar(path: Path, request: Mapping[str, Any]) -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location("openpine_worker_generated", path)
    if spec is None or spec.loader is None:
        raise IsolatedWorkerError(f"cannot load generated artifact: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strategy_cls = None
    for name in ("GeneratedStrategy", "GeneratedIndicator", "Strategy"):
        value = getattr(module, name, None)
        if isinstance(value, type) and callable(getattr(value, "_process_bar", None)):
            strategy_cls = value
            break
    if strategy_cls is None:
        for value in vars(module).values():
            if isinstance(value, type) and callable(getattr(value, "_process_bar", None)):
                strategy_cls = value
                break
    if strategy_cls is None:
        raise IsolatedWorkerError("generated artifact has no strategy class")

    class _Ctx:
        def __init__(self) -> None:
            self.intents: list[dict[str, object]] = []

        def entry(self, id: str, direction: str, qty: object = None, **kwargs: object) -> None:
            self.intents.append({"kind": "entry", "order_id": id, "qty": qty, "comment": kwargs.get("comment")})

        def order(self, id: str, direction: str, qty: object = None, **kwargs: object) -> None:
            self.intents.append({"kind": "order", "order_id": id, "qty": qty})

    class _Bar:
        def __init__(self, payload: Mapping[str, Any]) -> None:
            self.time = int(payload.get("time") or 0)
            self.open = payload.get("open")
            self.high = payload.get("high")
            self.low = payload.get("low")
            self.close = payload.get("close")
            self.volume = payload.get("volume")

    ctx = _Ctx()
    strategy = strategy_cls({}, None, ctx)
    bar = _Bar(request.get("bar") or {})
    strategy._process_bar(bar, int(request.get("bar_index") or 0))
    return {"ok": True, "intents": ctx.intents, "class_name": strategy_cls.__name__}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(json.dumps({"ok": False, "error": "missing generated path"}), flush=True)
        return 2
    path = Path(args[0])
    try:
        request = json.loads(sys.stdin.read() or "{}")
        action = request.get("action") or "process_bar"
        if action == "inspect":
            admit_generated_source(path)
            payload = {"ok": True, "path": str(path)}
        else:
            payload = _worker_process_bar(path, request)
        print(json.dumps(payload, separators=(",", ":")), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
