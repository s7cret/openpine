"""OS-isolated execution of generated artifact bytes.

Threat model: generated code is untrusted. The parent/gateway process never
imports it. The child receives already-captured bytes on stdin (no path reread).
Isolation is bubblewrap: new net/pid namespaces, read-only /usr, empty tmpfs
scratch, cleared environment. There is no in-process fallback.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openpine_contracts import validate_payload, verify_content_hash

from openpine.runtime.cgroup import CgroupError, attach_worker_tree, prepare_worker_cgroup
from openpine.runtime.worker_protocol import WorkerProtocolError, WorkerProtocolTranscript

ExecutionContext = dict[str, Any]
AdmittedManifest = Mapping[str, Any]

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

from copy import deepcopy
from openpine_contracts import (
    aggregate_batch_hash,
    seal_content_hash,
    validate_payload,
    validate_worker_protocol_sequence,
    verify_content_hash,
)

_COMPONENT = {
    "HELLO": "openpine", "LOAD_ARTIFACT": "openpine", "INIT_RUN": "openpine",
    "BAR_BEGIN": "openpine", "INTENT_BATCH": "pinelib",
    "BROKER_EVENT_BATCH": "backtest_engine", "RECALC_REQUEST": "backtest_engine",
    "RECALC_RESULT": "pinelib", "BAR_COMMIT": "backtest_engine",
    "FINALIZE": "openpine", "ABORT": "openpine",
}
_ROLE = {
    "HELLO": "worker", "LOAD_ARTIFACT": "parent", "INIT_RUN": "parent",
    "BAR_BEGIN": "parent", "INTENT_BATCH": "worker",
    "BROKER_EVENT_BATCH": "engine", "RECALC_REQUEST": "engine",
    "RECALC_RESULT": "worker", "BAR_COMMIT": "engine",
    "FINALIZE": "parent", "ABORT": "parent",
}
_ROLES = {kind: {role} for kind, role in _ROLE.items()}
_ROLES["ABORT"] = {"parent", "worker", "engine"}

def _component_for(kind, role):
    if kind == "ABORT" and role == "engine":
        return "backtest_engine"
    return _COMPONENT[kind]

_ALLOWED_AFTER = {
    "HELLO": {"LOAD_ARTIFACT", "ABORT"},
    "LOAD_ARTIFACT": {"INIT_RUN", "ABORT"},
    "INIT_RUN": {"BAR_BEGIN", "FINALIZE", "ABORT"},
    "BAR_BEGIN": {"INTENT_BATCH", "ABORT"},
    "INTENT_BATCH": {"BROKER_EVENT_BATCH", "BAR_COMMIT", "ABORT"},
    "BROKER_EVENT_BATCH": {"RECALC_REQUEST", "BAR_COMMIT", "ABORT"},
    "RECALC_REQUEST": {"RECALC_RESULT", "ABORT"},
    "RECALC_RESULT": {"INTENT_BATCH", "ABORT"},
    "BAR_COMMIT": {"BAR_BEGIN", "FINALIZE", "ABORT"},
    "FINALIZE": set(), "ABORT": set(),
}

def _semver(value):
    text = str(value)
    if "rc" in text and "-rc." not in text:
        base, marker, rc = text.partition("rc")
        if marker and base and rc.isdigit():
            return f"{base}-rc.{rc}"
    return text

class _Protocol:
    def __init__(self, execution_context):
        self.context = deepcopy(execution_context)
        validate_payload("openpine.execution_context.v1", self.context)
        if not verify_content_hash(self.context, schema_id="openpine.execution_context.v1"):
            raise RuntimeError("execution context content hash is invalid")
        commits = self.context["producer_commits"]
        versions = {
            row["name"]: _semver(row["version"])
            for row in self.context["wheel_identities"]
        }
        self.identities = {
            component: (versions[component], commits[component])
            for component in {"openpine", "pinelib", "backtest_engine"}
        }
        self.messages = []

    @property
    def last_id(self):
        return None if not self.messages else self.messages[-1]["message_id"]

    def _transition(self, kind):
        if not self.messages and kind != "HELLO":
            raise RuntimeError("worker protocol must start with HELLO")
        if self.messages and kind not in _ALLOWED_AFTER[self.messages[-1]["kind"]]:
            raise RuntimeError("invalid worker protocol transition")

    def append(self, kind, body, created_at_utc_ms):
        self._transition(kind)
        component = _COMPONENT[kind]
        version, commit = self.identities[component]
        sequence = len(self.messages)
        payload = seal_content_hash({
            "schema_id": "openpine.worker.protocol.v2",
            "schema_version": "2.3.0",
            "producer": component,
            "producer_version": version,
            "producer_commit": commit,
            "stack_id": self.context["stack_manifest_hash"],
            "created_at_utc_ms": int(created_at_utc_ms),
            "serializer_id": "openpine.canonical.json.v1",
            "content_hash_alg": "sha256",
            "message_id": f"{self.context['session_id']}:{sequence}:{kind}",
            "sender_role": _ROLE[kind],
            "session_id": self.context["session_id"],
            "run_id": self.context["run_id"],
            "sequence": sequence,
            "correlation_id": self.context["run_id"],
            "causation_id": self.last_id,
            "kind": kind,
            "body": deepcopy(body),
        }, schema_id="openpine.worker.protocol.v2")
        validate_payload("openpine.worker.protocol.v2", payload)
        candidate = [*self.messages, payload]
        if kind in {"FINALIZE", "ABORT"}:
            validate_worker_protocol_sequence(candidate)
        self.messages.append(payload)
        return payload

    def accept(self, message):
        validate_payload("openpine.worker.protocol.v2", message)
        if not verify_content_hash(message, schema_id="openpine.worker.protocol.v2"):
            raise RuntimeError("worker protocol content hash is invalid")
        kind = message["kind"]
        self._transition(kind)
        role = message.get("sender_role")
        if role not in _ROLES[kind]:
            raise RuntimeError("worker protocol sender role mismatch")
        component = _component_for(kind, role)
        version, commit = self.identities[component]
        expected = {
            "producer": component, "producer_version": version,
            "producer_commit": commit, "stack_id": self.context["stack_manifest_hash"],
            "session_id": self.context["session_id"], "run_id": self.context["run_id"],
            "sequence": len(self.messages), "correlation_id": self.context["run_id"],
            "causation_id": self.last_id, "sender_role": role,
        }
        if any(message.get(field) != value for field, value in expected.items()):
            raise RuntimeError("worker protocol identity mismatch")
        candidate = [*self.messages, message]
        if kind in {"FINALIZE", "ABORT"}:
            validate_worker_protocol_sequence(candidate)
        self.messages.append(deepcopy(message))
        return message

def _validated_ohlc_bar(raw_bar, label):
    if not isinstance(raw_bar, dict):
        raise RuntimeError(f"{label} must be an object")
    for name in ("time", "open", "high", "low", "close"):
        if name not in raw_bar or raw_bar[name] is None:
            raise RuntimeError(f"{label} required field {name} is missing")
    try:
        return (
            int(raw_bar["time"]),
            float(raw_bar["open"]),
            float(raw_bar["high"]),
            float(raw_bar["low"]),
            float(raw_bar["close"]),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} numeric fields are invalid") from exc

def _validated_htf_bar(raw_bar):
    values = _validated_ohlc_bar(raw_bar, "HTF bar")
    for name in ("symbol", "timeframe", "time_close"):
        if name not in raw_bar or raw_bar[name] is None or (
            name in {"symbol", "timeframe"} and not str(raw_bar[name]).strip()
        ):
            detail = f"HTF bar required field {name} is missing"
            if name == "time_close":
                detail += "; confirmed HTF bars require time_close"
            raise RuntimeError(detail)
    try:
        return values + (int(raw_bar["time_close"]),)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("HTF bar numeric fields are invalid") from exc

def _chart_timeframe_value(raw_bars, require_confirmed=False):
    intervals = []
    missing_time_close = False
    for raw_bar in raw_bars:
        time_close = raw_bar.get("time_close")
        if time_close is None:
            missing_time_close = True
            continue
        try:
            interval_ms = int(time_close) - int(raw_bar["time"]) + 1
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("chart bars require numeric time and time_close") from exc
        if interval_ms <= 0:
            raise RuntimeError("chart timeframe has non-positive inclusive duration")
        week_ms = 7 * 86_400_000
        if interval_ms % week_ms == 0:
            value = f"{interval_ms // week_ms}W"
        elif interval_ms % 86_400_000 == 0:
            value = f"{interval_ms // 86_400_000}D"
        elif interval_ms % 60_000 == 0:
            value = str(interval_ms // 60_000)
        elif interval_ms % 1_000 == 0:
            value = f"{interval_ms // 1_000}S"
        else:
            raise RuntimeError("chart timeframe has unsupported inclusive duration")
        intervals.append((interval_ms, value))
    if require_confirmed and (missing_time_close or not intervals):
        raise RuntimeError("request.security requires confirmed chart bars")
    if intervals and missing_time_close:
        raise RuntimeError("chart bars have partial time_close values")
    if intervals and any(interval != intervals[0][0] for interval, _value in intervals[1:]):
        raise RuntimeError("chart timeframe is inconsistent across bars")
    if intervals:
        return intervals[0][1]
    return "1"

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

def _entrypoint_shape(tree):
    for node in getattr(tree, "body", ()):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {
            item.name: item
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        process = methods.get("_process_bar")
        if process is None:
            continue
        positional = len(process.args.posonlyargs) + len(process.args.args) - 1
        process_arity = 2 if process.args.vararg is not None else positional
        if process_arity not in {1, 2}:
            raise RuntimeError("_process_bar entrypoint must accept bar or bar+bar_index")
        constructor = methods.get("__init__")
        if constructor is None:
            constructor_arity = 0
        else:
            constructor_arity = (
                len(constructor.args.posonlyargs) + len(constructor.args.args) - 1
            )
            if constructor.args.vararg is not None:
                constructor_arity = 2
            if constructor_arity not in {0, 1, 2}:
                raise RuntimeError("strategy constructor entrypoint shape is unsupported")
        return node.name, constructor_arity, process_arity
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
        "rlimits": {
            "address_space": list(resource.getrlimit(resource.RLIMIT_AS)),
            "cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
            "file_size": list(resource.getrlimit(resource.RLIMIT_FSIZE)),
            "processes": list(resource.getrlimit(resource.RLIMIT_NPROC)),
        },
    }

_CLOSED_TRADE_FIELDS = frozenset({
    "entry_price", "exit_price", "entry_time", "exit_time", "profit",
    "profit_percent", "commission", "qty", "side", "size", "entry_id",
    "exit_id", "entry_comment", "exit_comment", "max_runup", "max_drawdown",
    "entry_bar_index", "exit_bar_index",
})
_OPEN_TRADE_FIELDS = frozenset({
    "entry_price", "exit_price", "entry_time", "exit_time", "profit",
    "profit_percent", "commission", "qty", "side", "size", "entry_id",
    "exit_id", "max_runup", "max_drawdown", "entry_bar_index",
})

def _legacy_projection(sealed):
    validate_payload("openpine.broker_projection.v1", sealed)
    if not verify_content_hash(sealed, schema_id="openpine.broker_projection.v1"):
        raise RuntimeError("broker projection content hash is invalid")
    position = sealed["position"]
    open_trades = [
        {
            "entry_price": float(row["entry_price"]),
            "entry_time": row["entry_time_utc_ms"],
            "profit": float(row["unrealized_pnl"]),
            "commission": 0.0,
            "qty": float(row["qty"]),
            "side": row["direction"].lower(),
            "size": float(row["qty"]) * (1 if row["direction"] == "LONG" else -1),
            "entry_id": row["entry_name"],
            "entry_bar_index": row["entry_bar_index"],
        }
        for row in sealed["open_trades"]
    ]
    closed_trades = [
        {
            "entry_price": float(row["entry_price"]),
            "exit_price": float(row["exit_price"]),
            "entry_time": row["entry_time_utc_ms"],
            "exit_time": row["exit_time_utc_ms"],
            "profit": float(row["realized_pnl"]),
            "commission": float(row["commission"]),
            "qty": float(row["qty"]),
            "side": row["direction"].lower(),
            "size": float(row["qty"]) * (1 if row["direction"] == "LONG" else -1),
            "entry_id": row["entry_name"],
            "entry_bar_index": row["entry_bar_index"],
            "exit_bar_index": row["exit_bar_index"],
        }
        for row in sealed["closed_trades"]
    ]
    return {
        "cash": float(sealed["cash"]),
        "equity": float(sealed["equity"]),
        "netprofit": float(sealed["realized_pnl"]),
        "openprofit": float(sealed["unrealized_pnl"]),
        "grossprofit": float(sealed["gross_profit"]),
        "grossloss": float(sealed["gross_loss"]),
        "position_size": (
            float(position["qty"])
            * (-1 if position["direction"] == "SHORT" else 1)
        ),
        "position_avg_price": (
            0.0 if position["avg_price"] is None else float(position["avg_price"])
        ),
        "position_entry_name": position["entry_name"],
        "position_direction": position["direction"].lower(),
        "opentrades": len(open_trades),
        "closedtrades": len(closed_trades),
        "wintrades": sealed["winning_trades"],
        "losstrades": sealed["losing_trades"],
        "eventrades": sealed["even_trades"],
        "max_drawdown": float(sealed["max_drawdown"]),
        "max_runup": float(sealed["max_runup"]),
        "orders": sealed["orders"],
        "fills": sealed["fills"],
        "open_trade_log": open_trades,
        "closed_trade_log": closed_trades,
    }

class _LedgerProjection:
    def __init__(self, payload):
        if not isinstance(payload, dict):
            raise RuntimeError("broker projection must be an object")
        self._payload = payload

    def __getattr__(self, name):
        if name.startswith("closedtrades_"):
            field = name[len("closedtrades_"):]
            if field == "net_profit":
                field = "profit"
            collection_name = "closed_trade_log"
            allowed = _CLOSED_TRADE_FIELDS
        elif name.startswith("opentrades_"):
            field = name[len("opentrades_"):]
            collection_name = "open_trade_log"
            allowed = _OPEN_TRADE_FIELDS
        else:
            if name not in self._payload:
                raise AttributeError(name)
            return self._payload[name]
        if field not in allowed:
            raise AttributeError(name)

        def metric(index):
            from pinelib.core.na import na

            collection = self._payload.get(collection_name)
            if not isinstance(collection, list):
                raise RuntimeError(f"broker projection {collection_name} must be an array")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                return na
            if index >= len(collection):
                return na
            row = collection[index]
            if not isinstance(row, dict):
                raise RuntimeError(f"broker projection {collection_name}[{index}] must be an object")
            value = row.get(field)
            return na if value is None else value

        return metric

def _interactive_loop(
    inst,
    rt,
    process_bar_arity,
    PineBar,
    execution_context,
    generated_artifact,
):
    ctx = getattr(inst, "ctx", None)
    if ctx is None:
        raise RuntimeError("interactive strategy must expose ctx")
    if getattr(ctx, "_runtime", None) is None:
        attach_runtime = getattr(ctx, "attach_runtime", None)
        if not callable(attach_runtime):
            raise RuntimeError("interactive strategy context cannot attach runtime")
        attach_runtime(rt)
    tape = getattr(ctx, "intent_tape", None)
    if tape is None:
        raise RuntimeError("interactive strategy must expose an intent tape")
    if list(tape.events):
        raise RuntimeError("strategy constructor emitted intents before protocol admission")
    initialize = getattr(ctx, "_initialize_intent_context", None)
    if not callable(initialize):
        raise RuntimeError("strategy context cannot admit execution identity")
    initialize(
        run_id=execution_context["run_id"],
        strategy_id=execution_context["strategy_id"],
        series_id=execution_context["series_id"],
        instrument_id=execution_context["instrument_id"],
        timeframe=execution_context["timeframe"],
        producer_commit=execution_context["producer_commits"]["pinelib"],
        strict_production=True,
        stack_id=execution_context["stack_manifest_hash"],
        execution_context=execution_context,
    )
    ctx.attach_runtime(rt)
    tape = ctx.intent_tape
    protocol = _Protocol(execution_context)
    current_key = None
    current_bar = None
    sent_events = 0
    last_intent_message = None

    def commit_current():
        nonlocal current_key, current_bar
        if current_key is None:
            return
        commit = getattr(ctx, "commit_intents_for_current_bar", None)
        if callable(commit):
            commit()
        end_bar = getattr(rt, "end_bar", None)
        if callable(end_bar):
            end_bar()
        commit_history = getattr(ctx, "commit_scalar_history", None)
        if callable(commit_history):
            commit_history()
        current_key = None
        current_bar = None

    hello = protocol.append(
        "HELLO",
        {
            "worker_id": execution_context["session_id"],
            "protocol_version": "2.3.0",
            "capabilities": ["closed_bar", "checkpoint_v1"],
        },
        0,
    )
    json.dump(hello, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()

    loaded = False
    initialized = False
    for line in sys.stdin:
        if len(line) > 1_000_000:
            raise RuntimeError("interactive message exceeds size limit")
        message = json.loads(line)
        protocol.accept(message)
        kind = message["kind"]
        body = message["body"]
        if kind == "LOAD_ARTIFACT":
            if (
                body["artifact_hash"] != generated_artifact["content_hash"]
                or body["module_hash"] != generated_artifact["emitted_module_hash"]
                or body["entrypoint_module"] != generated_artifact["entrypoint_module"]
                or body["entrypoint_class"] != generated_artifact["entrypoint_class"]
            ):
                raise RuntimeError("loaded artifact identity mismatch")
            loaded = True
            continue
        if kind == "INIT_RUN":
            if not loaded:
                raise RuntimeError("artifact must be loaded before run initialization")
            if (
                body["execution_context"] != execution_context
                or body["execution_context_hash"] != execution_context["content_hash"]
                or body["run_id"] != execution_context["run_id"]
            ):
                raise RuntimeError("run initialization identity mismatch")
            initialized = True
            continue
        if kind == "FINALIZE":
            commit_current()
            return 0
        if kind == "ABORT":
            commit_current()
            return 2
        if not initialized:
            raise RuntimeError("run must be initialized before bar execution")
        if kind == "BROKER_EVENT_BATCH":
            continue
        if kind == "RECALC_REQUEST":
            if current_bar is None or last_intent_message is None:
                raise RuntimeError("recalculation has no active callback")
            projection = _LedgerProjection(
                _legacy_projection(body["broker_projection"])
            )
            ctx.attach_strategy_ledger_view(projection)
            if "closedtrades" in projection._payload:
                ctx.closedtrades = int(projection._payload["closedtrades"])
            recalc_iteration = body["recalc_iteration"]
            begin_callback = getattr(ctx, "begin_intent_callback", None)
            if callable(begin_callback):
                begin_callback(phase="score", recalc_iteration=recalc_iteration)
            if process_bar_arity == 1:
                inst._process_bar(current_bar)
            else:
                inst._process_bar(current_bar, body["bar_index"])
            recalc_result = protocol.append(
                "RECALC_RESULT",
                {
                    "run_id": execution_context["run_id"],
                    "bar_index": body["bar_index"],
                    "recalc_iteration": recalc_iteration,
                    "intent_batch_message_id": last_intent_message["message_id"],
                    "intent_batch_hash": last_intent_message["body"]["intent_batch_hash"],
                },
                current_bar.time,
            )
            json.dump(recalc_result, sys.stdout)
            sys.stdout.write("\n")
            all_events = list(tape.events)
            batch = [_safe(dict(item)) for item in all_events[sent_events:]]
            sent_events = len(all_events)
            response = protocol.append(
                "INTENT_BATCH",
                {
                    "run_id": execution_context["run_id"],
                    "bar_index": body["bar_index"],
                    "recalc_iteration": recalc_iteration,
                    "intent_batch_hash": aggregate_batch_hash(
                        batch,
                        batch_kind="INTENT_BATCH",
                        item_schema_id="openpine.intent.v2",
                    ),
                    "intents": batch,
                },
                current_bar.time,
            )
            last_intent_message = response
            json.dump(response, sys.stdout)
            sys.stdout.write("\n")
            sys.stdout.flush()
            continue
        if kind == "BAR_COMMIT":
            commit_current()
            continue
        if kind != "BAR_BEGIN":
            raise RuntimeError("unsupported interactive message kind")

        raw_bar = body["bar"]
        projection_payload = body["broker_projection"]
        if not verify_content_hash(raw_bar, schema_id="openpine.marketdata.bar.v2"):
            raise RuntimeError("bar content hash is invalid")
        if body["bar_hash"] != raw_bar["bar_content_hash"]:
            raise RuntimeError("bar identity mismatch")
        time = int(raw_bar["open_time_utc_ms"])
        open_ = float(raw_bar["open"])
        high = float(raw_bar["high"])
        low = float(raw_bar["low"])
        close = float(raw_bar["close"])
        bar_index = body["bar_index"]
        recalc_iteration = body["recalc_iteration"]
        key = (bar_index, time)
        if current_key is not None and current_key != key:
            raise RuntimeError("previous bar must be committed before BAR_BEGIN")
        if current_key is None:
            current_bar = PineBar(
                time=time,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=float(raw_bar["volume"]),
                time_close=int(raw_bar["close_time_utc_ms"]),
            )
            rt.begin_bar(current_bar)
            current_key = key

        projection = _LedgerProjection(_legacy_projection(projection_payload))
        ctx.attach_strategy_ledger_view(projection)
        if "closedtrades" in projection._payload:
            ctx.closedtrades = int(projection._payload["closedtrades"])
        begin_callback = getattr(ctx, "begin_intent_callback", None)
        if callable(begin_callback):
            begin_callback(phase="score", recalc_iteration=recalc_iteration)
        if process_bar_arity == 1:
            inst._process_bar(current_bar)
        else:
            inst._process_bar(current_bar, bar_index)

        all_events = list(tape.events)
        batch = [_safe(dict(item)) for item in all_events[sent_events:]]
        sent_events = len(all_events)
        intent_batch_hash = aggregate_batch_hash(
            batch,
            batch_kind="INTENT_BATCH",
            item_schema_id="openpine.intent.v2",
        )
        response = protocol.append(
            "INTENT_BATCH",
            {
                "run_id": execution_context["run_id"],
                "bar_index": bar_index,
                "recalc_iteration": recalc_iteration,
                "intent_batch_hash": intent_batch_hash,
                "intents": batch,
            },
            time,
        )
        last_intent_message = response
        json.dump(response, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()
    commit_current()
    return 0

def main() -> int:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (134217728, 134217728))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
    except (ValueError, resource.error):
        pass
    raw = sys.stdin.readline(1_000_000)
    request = json.loads(raw)
    if request.get("interactive") is True:
        from openpine_contracts import validate_payload, verify_content_hash

        execution_context = request.get("execution_context")
        try:
            validate_payload("openpine.execution_context.v1", execution_context)
        except Exception:
            json.dump({"ok": False, "error": "execution_context invalid"}, sys.stdout)
            return 2
        if not verify_content_hash(
            execution_context, schema_id="openpine.execution_context.v1"
        ) or request.get("stack_id") != execution_context.get("stack_manifest_hash"):
            json.dump({"ok": False, "error": "stack_id mismatch"}, sys.stdout)
            return 2
        generated_artifact = request.get("generated_artifact")
        try:
            validate_payload("openpine.generated_artifact.v2", generated_artifact)
        except Exception:
            json.dump({"ok": False, "error": "generated artifact invalid"}, sys.stdout)
            return 2
        if not verify_content_hash(
            generated_artifact, schema_id="openpine.generated_artifact.v2"
        ):
            json.dump({"ok": False, "error": "generated artifact hash invalid"}, sys.stdout)
            return 2
        from ast2python.artifact import _digest
        if generated_artifact.get("emitted_module_hash") != _digest(
            request.get("source", ""), "openpine.generated_artifact.v2"
        ):
            json.dump({"ok": False, "error": "generated module hash mismatch"}, sys.stdout)
            return 2
    elif request.get("stack_id") != "openpine-5.0":
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
    try:
        entrypoint_shape = _entrypoint_shape(tree)
    except RuntimeError as exc:
        json.dump({
            "ok": False,
            "error_code": "ARTIFACT_ENTRYPOINT_ERROR",
            "error": str(exc),
        }, sys.stdout)
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
    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        json.dump({"ok": False, "error": "params must be an object"}, sys.stdout)
        return 2
    if bars or request.get("interactive") is True:
        cls = None
        constructor_arity = None
        process_bar_arity = None
        if entrypoint_shape is not None:
            entrypoint_name, constructor_arity, process_bar_arity = entrypoint_shape
            candidate = namespace.get(entrypoint_name)
            if isinstance(candidate, type) and callable(
                getattr(candidate, "_process_bar", None)
            ):
                cls = candidate
        if request.get("interactive") is True and cls is None:
            json.dump({
                "ok": False,
                "error_code": "ARTIFACT_ENTRYPOINT_ERROR",
                "error": "interactive artifact must declare one generated strategy entrypoint",
            }, sys.stdout)
            return 2
        if cls is not None:
            try:
                from pinelib.core import Bar as PineBar, PineRuntime
                from pinelib.core.types import RuntimeConfig, SymbolInfo, TimeframeInfo
                stamped = request.get("htf_bars") or []
                for raw_bar in bars:
                    _validated_ohlc_bar(raw_bar, "chart bar")
                if request.get("interactive") is True:
                    from pinelib.execution_context import ExecutionContext as PineExecutionContext

                    symbol_info = PineExecutionContext.coerce(
                        execution_context
                    ).to_symbol_info()
                else:
                    symbol_info = SymbolInfo(tickerid=str(request["instrument_id"]))
                rt = PineRuntime(
                    symbol_info=symbol_info,
                    timeframe=TimeframeInfo.from_string(
                        str(request.get("chart_timeframe"))
                        if request.get("interactive") is True
                        else _chart_timeframe_value(bars, require_confirmed=bool(stamped))
                    ),
                    config=RuntimeConfig(semantic_profile=request.get("semantic_profile")),
                )
                class _NoHtfProvider:
                    def get_bars(self, *a, **k):
                        raise RuntimeError("request.security requires confirmed HTF bars")
                if stamped:
                    from pinelib.request.providers import InMemoryDataProvider
                    keyed = {}
                    for item in stamped:
                        time, open_, high, low, close, time_close = _validated_htf_bar(item)
                        key = (str(item["symbol"]), str(item["timeframe"]))
                        keyed.setdefault(key, []).append(
                            PineBar(
                                time=time,
                                open=open_,
                                high=high,
                                low=low,
                                close=close,
                                volume=float(item.get("volume") or 0),
                                time_close=time_close,
                            )
                        )
                    rt.data_provider = InMemoryDataProvider(keyed)
                else:
                    rt.data_provider = _NoHtfProvider()
            except Exception as exc:
                json.dump({"ok": False, "error": f"pine runtime: {exc}"}, sys.stdout)
                return 2
            try:
                if constructor_arity == 0:
                    inst = cls()
                elif constructor_arity == 1:
                    inst = cls(params)
                else:
                    inst = cls(params=params, runtime=rt)
            except Exception as exc:
                json.dump({
                    "ok": False,
                    "error_code": "STRATEGY_CONSTRUCTOR_ERROR",
                    "error": f"{exc.__class__.__name__}: {exc}",
                }, sys.stdout)
                return 2
            if request.get("interactive") is True:
                try:
                    return _interactive_loop(
                        inst,
                        rt,
                        process_bar_arity,
                        PineBar,
                        execution_context,
                        request["generated_artifact"],
                    )
                except Exception as exc:
                    json.dump({
                        "ok": False,
                        "kind": "ABORT",
                        "error_code": "WORKER_PROTOCOL_ERROR",
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }, sys.stdout)
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return 2
            events = []
            for i, raw_bar in enumerate(bars):
                try:
                    time, open_, high, low, close = _validated_ohlc_bar(raw_bar, "chart bar")
                    bar = PineBar(
                        time=time,
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=float(raw_bar.get("volume") or 0),
                        time_close=(
                            int(raw_bar["time_close"])
                            if raw_bar.get("time_close") is not None
                            else None
                        ),
                    )
                    rt.begin_bar(bar)
                except Exception as exc:
                    json.dump({
                        "ok": False,
                        "error_code": "BAR_INPUT_ERROR",
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }, sys.stdout)
                    return 2
                ctx = getattr(inst, "ctx", None)
                if ctx is not None and getattr(ctx, "_runtime", None) is None:
                    ctx._runtime = rt
                try:
                    if process_bar_arity == 1:
                        inst._process_bar(bar)
                    else:
                        inst._process_bar(bar, i)
                except Exception as exc:
                    json.dump({
                        "ok": False,
                        "error_code": "STRATEGY_RUNTIME_ERROR",
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }, sys.stdout)
                    return 2
                end_bar = getattr(rt, "end_bar", None)
                if callable(end_bar):
                    try:
                        end_bar()
                    except Exception as exc:
                        json.dump({
                            "ok": False,
                            "error_code": "BAR_COMMIT_ERROR",
                            "error": f"{exc.__class__.__name__}: {exc}",
                        }, sys.stdout)
                        return 2
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
_TRUSTED_NAMES = (
    "ast2python",
    "attr",
    "attrs",
    "jsonschema",
    "jsonschema_specifications",
    "openpine_contracts",
    "pinelib",
    "referencing",
    "rpds",
    "typing_extensions",
)
_RUNTIME_ROOTS = ("/usr", "/lib", "/lib64")


def _chmod_tree(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        mode = 0o755 if path.is_dir() else 0o644
        path.chmod(mode | stat.S_IROTH | (stat.S_IXOTH if path.is_dir() else 0))


def _cleanup_trusted_stage() -> None:
    global _TRUSTED_STAGE
    stage = _TRUSTED_STAGE
    _TRUSTED_STAGE = None
    if stage is not None:
        shutil.rmtree(stage, ignore_errors=True)


atexit.register(_cleanup_trusted_stage)


def _stage_trusted_packages() -> list[tuple[str, str]]:
    global _TRUSTED_STAGE
    dest_root = Path(TRUSTED_DEST)
    if _TRUSTED_STAGE is None:
        stage = Path(tempfile.mkdtemp(prefix="openpine-trusted-"))
        try:
            stage.chmod(0o755)
            for name in _TRUSTED_NAMES:
                spec = importlib.util.find_spec(name)
                if spec is None or not spec.origin:
                    continue
                origin = Path(spec.origin).resolve()
                if spec.submodule_search_locations is None:
                    target = stage / origin.name
                    shutil.copy2(origin, target)
                    target.chmod(0o644 | stat.S_IROTH)
                else:
                    src = origin.parent
                    target = stage / name
                    shutil.copytree(
                        src,
                        target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )
                    _chmod_tree(target)
            _TRUSTED_STAGE = stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return [(str(_TRUSTED_STAGE), str(dest_root))]


def worker_user_uid() -> int | None:
    try:
        probe = subprocess.run(  # noqa: S603
            ["/usr/bin/sudo", "-n", "-u", WORKER_USER, "--", "/usr/bin/id", "-u"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode != 0 or not probe.stdout.strip().isdigit():
        return None
    uid = int(probe.stdout.strip())
    return uid if uid > 0 else None


def worker_user_available() -> bool:
    return worker_user_uid() is not None


def _runtime_ro_bind_args() -> list[str]:
    argv: list[str] = []
    roots = [Path(root) for root in _RUNTIME_ROOTS if Path(root).exists()]
    python_prefix = Path(sys.base_prefix).resolve()
    if not any(python_prefix.is_relative_to(root.resolve()) for root in roots):
        roots.append(python_prefix)
    for root in roots:
        argv.extend(["--ro-bind", str(root), str(root)])
    return argv


def _resolved_worker_policy(admitted_manifest: AdmittedManifest) -> dict[str, Any]:
    if not isinstance(admitted_manifest, Mapping):
        raise IsolatedWorkerError("sealed admitted manifest is required")
    manifest_hash = admitted_manifest.get("manifest_hash")
    if not isinstance(manifest_hash, str) or not manifest_hash.startswith("sha256:"):
        raise IsolatedWorkerError("sealed admitted manifest hash is required")
    policy = admitted_manifest.get("worker_policy")
    if not isinstance(policy, Mapping):
        raise IsolatedWorkerError("admitted worker policy is required")
    required = {
        "bubblewrap_path",
        "python_path",
        "worker_user",
        "tmpfs_bytes",
        "memory_max_bytes",
        "tasks_max",
        "trusted_packages",
    }
    if set(policy) != required:
        raise IsolatedWorkerError("admitted worker policy fields are invalid")
    if policy.get("python_path") != "candidate-python":
        raise IsolatedWorkerError("admitted sandbox Python policy is invalid")
    trusted = policy.get("trusted_packages")
    if trusted != list(_TRUSTED_NAMES):
        raise IsolatedWorkerError("admitted trusted package policy is invalid")
    resolved = dict(policy)
    resolved["python_path"] = str(Path(sys.executable).resolve())
    resolved["trusted_package_binds"] = _stage_trusted_packages()
    return resolved


def _bwrap_argv(
    admitted_manifest: AdmittedManifest, unit_name: str | None = None
) -> list[str]:
    policy = _resolved_worker_policy(admitted_manifest)
    if not Path(str(policy["bubblewrap_path"])).is_file():
        raise IsolatedWorkerError("bubblewrap is required for isolated execution")
    if not Path(str(policy["python_path"])).is_file():
        raise IsolatedWorkerError("sandbox python is missing")
    if worker_user_uid() is None:
        raise IsolatedWorkerError("dedicated openpine-worker user is required")
    unit = unit_name or "openpine-worker-sandbox-test"
    prefix = [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--pipe",
        "--service-type=exec",
        f"--unit={unit}",
        f"--uid={policy['worker_user']}",
        f"--property=MemoryMax={policy['memory_max_bytes']}",
        "--property=MemorySwapMax=0",
        f"--property=TasksMax={policy['tasks_max']}",
        "--property=CPUQuota=100%",
        "--property=KillMode=control-group",
        "--property=OOMPolicy=kill",
        "--property=SystemCallFilter=@system-service @mount",
        "--",
    ]
    argv = prefix + [
        str(policy["bubblewrap_path"]),
        *_runtime_ro_bind_args(),
        "--size",
        str(policy["tmpfs_bytes"]),
        "--tmpfs",
        "/tmp",
    ]
    for src, dest in policy["trusted_package_binds"]:
        argv.extend(["--ro-bind", src, dest])
    return argv + [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--remount-ro",
        "/",
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
        str(policy["python_path"]),
        "-I",
        "-c",
        _BOOTSTRAP,
    ]


def _worker_unit_name() -> str:
    return f"openpine-worker-{uuid.uuid4().hex}"


_PENDING_WORKER_UNITS: set[str] = set()
_PENDING_WORKER_UNITS_LOCK = threading.Lock()


def _retain_pending_worker_unit(unit_name: str) -> None:
    with _PENDING_WORKER_UNITS_LOCK:
        _PENDING_WORKER_UNITS.add(unit_name)


def _discard_pending_worker_unit(unit_name: str) -> None:
    with _PENDING_WORKER_UNITS_LOCK:
        _PENDING_WORKER_UNITS.discard(unit_name)


def _stop_worker_unit(unit_name: str) -> None:
    def run_systemctl(
        *args: str, capture_stdout: bool = False
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603
                ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", *args, unit_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsolatedWorkerError("worker unit cleanup failed") from exc

    def active_state() -> str:
        completed = run_systemctl(
            "show", "--property=ActiveState", "--value", capture_stdout=True
        )
        if completed.returncode != 0:
            raise IsolatedWorkerError("worker unit state verification failed")
        state = (completed.stdout or "").strip()
        if not state:
            raise IsolatedWorkerError("worker unit state verification was empty")
        return state

    stop_error: IsolatedWorkerError | None = None
    try:
        run_systemctl("stop")
    except IsolatedWorkerError as exc:
        stop_error = exc

    try:
        if active_state() == "inactive":
            _discard_pending_worker_unit(unit_name)
            return
    except IsolatedWorkerError:
        pass

    kill_error: IsolatedWorkerError | None = None
    try:
        run_systemctl("kill", "--kill-who=all", "--signal=KILL")
    except IsolatedWorkerError as exc:
        kill_error = exc

    try:
        final_state = active_state()
    except IsolatedWorkerError as exc:
        _retain_pending_worker_unit(unit_name)
        raise IsolatedWorkerError("worker unit cleanup could not be verified") from (
            kill_error or stop_error or exc
        )
    if final_state != "inactive":
        _retain_pending_worker_unit(unit_name)
        raise IsolatedWorkerError("worker unit remained active after cleanup") from (
            kill_error or stop_error
        )
    _discard_pending_worker_unit(unit_name)


def _retry_pending_worker_unit_cleanup() -> None:
    with _PENDING_WORKER_UNITS_LOCK:
        pending = tuple(_PENDING_WORKER_UNITS)
    for unit_name in pending:
        try:
            _stop_worker_unit(unit_name)
        except IsolatedWorkerError:
            pass


atexit.register(_retry_pending_worker_unit_cleanup)


def _close_process_pipes(proc: subprocess.Popen[Any]) -> None:
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


def _reap_worker_process_bounded(
    proc: subprocess.Popen[Any], timeout: float = 2.0
) -> None:
    cleanup_error: BaseException | None = None
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup_error = exc
    finally:
        _close_process_pipes(proc)
    if cleanup_error is not None:
        raise IsolatedWorkerError("worker process cleanup did not complete") from cleanup_error


def _cleanup_worker_process(proc: subprocess.Popen[Any], unit_name: str) -> None:
    process_kill_error: OSError | None = None
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError as exc:
            process_kill_error = exc
    unit_error: IsolatedWorkerError | None = None
    try:
        _stop_worker_unit(unit_name)
    except IsolatedWorkerError as exc:
        unit_error = exc
    reap_error: IsolatedWorkerError | None = None
    try:
        _reap_worker_process_bounded(proc)
    except IsolatedWorkerError as exc:
        reap_error = exc
    if unit_error is not None:
        raise unit_error from (reap_error or process_kill_error)
    if reap_error is not None:
        raise reap_error from process_kill_error


def _read_available_stderr(proc: subprocess.Popen[Any], limit: int = 1_000_000) -> str:
    pipe = proc.stderr
    if pipe is None or pipe.closed:
        return ""
    chunks = bytearray()
    while len(chunks) < limit:
        ready, _, _ = select.select([pipe], [], [], 0)
        if not ready:
            break
        try:
            chunk = os.read(pipe.fileno(), min(65_536, limit - len(chunks)))
        except OSError:
            break
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks).decode("utf-8", errors="replace")


class InteractiveWorkerSession:
    """Persistent protocol-v2 worker: one broker projection and bar per message."""

    def __init__(
        self,
        source: bytes,
        execution_context: ExecutionContext,
        instrument_id: str,
        admitted_manifest: AdmittedManifest,
        generated_artifact: dict[str, Any],
        run_hash: str,
        protocol_artifact_dir: str | Path,
        *,
        semantic_profile: str,
        chart_timeframe: str,
        params: dict[str, Any] | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
        timeout_s: float = 5.0,
        cgroup_dir: str | Path | None = None,
    ) -> None:
        if len(source) > 500_000:
            raise IsolatedWorkerError("artifact source exceeds size limit")
        try:
            validate_payload("openpine.execution_context.v1", execution_context)
        except ValueError as exc:
            raise IsolatedWorkerError("execution_context is invalid") from exc
        if not verify_content_hash(
            execution_context, schema_id="openpine.execution_context.v1"
        ):
            raise IsolatedWorkerError("execution_context content hash is invalid")
        try:
            validate_payload("openpine.generated_artifact.v2", generated_artifact)
        except ValueError as exc:
            raise IsolatedWorkerError("generated artifact is invalid") from exc
        if not verify_content_hash(
            generated_artifact, schema_id="openpine.generated_artifact.v2"
        ):
            raise IsolatedWorkerError("generated artifact content hash is invalid")
        from ast2python.artifact import _digest

        if generated_artifact.get("emitted_module_hash") != _digest(
            source.decode("utf-8"), "openpine.generated_artifact.v2"
        ):
            raise IsolatedWorkerError("generated artifact module hash mismatch")
        if (
            execution_context.get("generated_artifact_hash")
            != generated_artifact.get("content_hash")
            or execution_context.get("emitted_module_hash")
            != generated_artifact.get("emitted_module_hash")
        ):
            raise IsolatedWorkerError("generated artifact admission identity mismatch")
        if generated_artifact.get("stack_id") != "openpine-5.0":
            raise IsolatedWorkerError("generated artifact stack family mismatch")
        generated_commits = generated_artifact.get("producer_commits")
        execution_commits = execution_context.get("producer_commits")
        if not isinstance(generated_commits, Mapping) or not isinstance(
            execution_commits, Mapping
        ):
            raise IsolatedWorkerError("generated artifact producer identity is missing")
        for component_name in (
            "openpine-contracts",
            "pine2ast",
            "ast2python",
            "pinelib",
        ):
            if generated_commits.get(component_name) != execution_commits.get(
                component_name
            ):
                raise IsolatedWorkerError(
                    f"generated artifact producer identity mismatch: {component_name}"
                )
        if (
            not isinstance(run_hash, str)
            or not run_hash.startswith("sha256:")
            or run_hash == "sha256:" + "0" * 64
        ):
            raise IsolatedWorkerError("sealed run hash is required")
        stack_manifest_hash = execution_context.get("stack_manifest_hash")
        if stack_manifest_hash != execution_context.get("stack_id"):
            raise IsolatedWorkerError("execution_context stack manifest identity mismatch")
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise IsolatedWorkerError("instrument identity is required")
        if semantic_profile not in {"legacy_4x", "strict_5x"}:
            raise IsolatedWorkerError("semantic_profile required")
        if not isinstance(chart_timeframe, str) or not chart_timeframe.strip():
            raise IsolatedWorkerError("chart_timeframe required")
        if params is not None and not isinstance(params, dict):
            raise IsolatedWorkerError("params must be an object")
        if not str(protocol_artifact_dir):
            raise IsolatedWorkerError("protocol artifact directory is required")
        self.timeout_s = timeout_s
        self._closed = False
        self._stdout_buffer = bytearray()
        self.unit_name = _worker_unit_name()
        self.bytes_sent = 0
        self.bytes_received = 0
        self.generated_artifact = dict(generated_artifact)
        self.run_hash = run_hash
        self.protocol_artifact_dir = Path(protocol_artifact_dir)
        self.protocol_artifact_dir.mkdir(parents=True, exist_ok=True)
        self.protocol = WorkerProtocolTranscript(execution_context)
        self._last_commit: dict[str, Any] | None = None
        if cgroup_dir is not None:
            try:
                prepare_worker_cgroup(cgroup_dir)
            except CgroupError as exc:
                raise IsolatedWorkerError(str(exc)) from exc
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                _bwrap_argv(admitted_manifest, self.unit_name),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise IsolatedWorkerError("worker spawn failed") from exc
        if cgroup_dir is not None:
            try:
                attach_worker_tree(cgroup_dir, self.proc.pid)
            except CgroupError as exc:
                self._kill()
                raise IsolatedWorkerError(str(exc)) from exc
        try:
            self._write_bootstrap(
                {
                    "interactive": True,
                    "source": source.decode("utf-8"),
                    "stack_id": stack_manifest_hash,
                    "execution_context": execution_context,
                    "generated_artifact": generated_artifact,
                    "instrument_id": instrument_id,
                    "semantic_profile": semantic_profile,
                    "chart_timeframe": chart_timeframe,
                    "htf_bars": htf_bars or [],
                    "params": {} if params is None else params,
                }
            )
            hello = self._read_message()
            if hello.get("kind") != "HELLO":
                self._raise_response(hello)
            self.hello = hello
            load = self.protocol.append(
                "LOAD_ARTIFACT",
                {
                    "artifact_hash": generated_artifact["content_hash"],
                    "module_hash": generated_artifact["emitted_module_hash"],
                    "entrypoint_module": generated_artifact["entrypoint_module"],
                    "entrypoint_class": generated_artifact["entrypoint_class"],
                },
                created_at_utc_ms=0,
            )
            self._write_message(load)
            init = self.protocol.append(
                "INIT_RUN",
                {
                    "run_id": execution_context["run_id"],
                    "run_hash": run_hash,
                    "execution_context_hash": execution_context["content_hash"],
                    "execution_context": execution_context,
                    "semantic_profile": semantic_profile,
                    "capabilities": ["closed_bar", "checkpoint_v1"],
                },
                created_at_utc_ms=0,
            )
            self._write_message(init)
        except Exception:
            self._kill()
            raise

    def _kill(self) -> None:
        if getattr(self, "proc", None) is None:
            return
        try:
            _cleanup_worker_process(self.proc, self.unit_name)
        finally:
            self._closed = True

    def _close_pipes(self) -> None:
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()

    def _write_json_line(self, payload: dict[str, Any]) -> None:
        if self._closed or self.proc.stdin is None:
            raise IsolatedWorkerError("interactive worker is closed")
        encoded = json.dumps(payload, separators=(",", ":")) + "\n"
        encoded_size = len(encoded.encode("utf-8"))
        if encoded_size > 1_000_000:
            raise IsolatedWorkerError("interactive message exceeds size limit")
        try:
            self.proc.stdin.write(encoded)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise IsolatedWorkerError("interactive worker pipe closed") from exc
        self.bytes_sent += encoded_size

    def _write_bootstrap(self, payload: dict[str, Any]) -> None:
        self._write_json_line(payload)

    def _write_message(self, payload: dict[str, Any]) -> None:
        try:
            validate_payload("openpine.worker.protocol.v2", payload)
        except ValueError as exc:
            raise IsolatedWorkerError("invalid worker protocol message") from exc
        if not verify_content_hash(payload, schema_id="openpine.worker.protocol.v2"):
            raise IsolatedWorkerError("worker protocol message content hash is invalid")
        self._write_json_line(payload)

    def _read_message(self) -> dict[str, Any]:
        if self.proc.stdout is None:
            raise IsolatedWorkerError("interactive worker stdout unavailable")
        deadline = time.monotonic() + self.timeout_s
        newline = self._stdout_buffer.find(b"\n")
        while newline < 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise IsolatedWorkerError("timeout")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                self._kill()
                raise IsolatedWorkerError("timeout")
            try:
                chunk = os.read(self.proc.stdout.fileno(), 65_536)
            except OSError as exc:
                raise IsolatedWorkerError("interactive worker stdout read failed") from exc
            if not chunk:
                stderr = _read_available_stderr(self.proc)
                raise IsolatedWorkerError(stderr.strip() or "interactive worker exited")
            self._stdout_buffer.extend(chunk)
            newline = self._stdout_buffer.find(b"\n")
            if newline < 0 and len(self._stdout_buffer) > 1_000_000:
                self._kill()
                raise IsolatedWorkerError("excessive worker output")
        line_bytes = bytes(self._stdout_buffer[: newline + 1])
        del self._stdout_buffer[: newline + 1]
        line_size = len(line_bytes)
        if line_size > 1_000_000:
            self._kill()
            raise IsolatedWorkerError("excessive worker output")
        self.bytes_received += line_size
        try:
            response = json.loads(line_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IsolatedWorkerError("malformed worker output") from exc
        if not isinstance(response, dict):
            raise IsolatedWorkerError("worker response must be an object")
        if response.get("schema_id") != "openpine.worker.protocol.v2":
            self._raise_response(response)
        try:
            validate_payload("openpine.worker.protocol.v2", response)
            if not verify_content_hash(
                response, schema_id="openpine.worker.protocol.v2"
            ):
                raise WorkerProtocolError("worker response content hash is invalid")
            self.protocol.accept(response)
        except (ValueError, WorkerProtocolError) as exc:
            raise IsolatedWorkerError("invalid worker protocol response") from exc
        return response

    @staticmethod
    def _raise_response(response: dict[str, Any]) -> None:
        code = str(response.get("error_code") or "WORKER_REJECTED")
        detail = str(response.get("error") or "worker rejected message")
        raise IsolatedWorkerError(f"{code}: {detail}")

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._write_message(payload)
        return self._read_message()

    def evaluate_bar(self, event: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "run_id",
            "bar_index",
            "bar_open_time_utc_ms",
            "recalc_iteration",
            "bar_hash",
            "bar",
            "broker_projection",
        }
        if not required.issubset(event):
            raise IsolatedWorkerError("engine BAR_BEGIN artifact is incomplete")
        message = self.protocol.append(
            "BAR_BEGIN",
            {name: event[name] for name in required},
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        response = self._request(message)
        if response.get("kind") != "INTENT_BATCH":
            raise IsolatedWorkerError("worker did not return INTENT_BATCH")
        body = response.get("body")
        if not isinstance(body, dict):
            raise IsolatedWorkerError("worker INTENT_BATCH body is invalid")
        return body

    def _persist_artifact(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        encoded = artifact.get("bytes")
        if not isinstance(encoded, bytes):
            raise IsolatedWorkerError("protocol artifact bytes are required")
        artifact_hash = artifact.get("artifact_hash")
        if not isinstance(artifact_hash, str) or not artifact_hash.startswith("sha256:"):
            raise IsolatedWorkerError("protocol artifact hash is invalid")
        path = self.protocol_artifact_dir / f"{artifact_hash[7:]}.json"
        if path.exists():
            if path.read_bytes() != encoded:
                raise IsolatedWorkerError("protocol artifact hash collision")
        else:
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(encoded)
            temporary.replace(path)
        return {
            "artifact_hash": artifact_hash,
            "schema_id": artifact["schema_id"],
            "codec": artifact["codec"],
            "size_bytes": artifact["size_bytes"],
            "uri": path.resolve().as_uri(),
        }

    def evaluate_recalc(self, event: Mapping[str, Any]) -> dict[str, Any]:
        recalc_iteration = int(event["recalc_iteration"])
        broker_batch = self.protocol.append(
            "BROKER_EVENT_BATCH",
            {
                "run_id": event["run_id"],
                "bar_index": event["bar_index"],
                "recalc_iteration": recalc_iteration - 1,
                "broker_event_batch_hash": event["broker_event_batch_hash"],
                "broker_events": event["broker_events"],
            },
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        self._write_message(broker_batch)
        request = self.protocol.append(
            "RECALC_REQUEST",
            {
                "run_id": event["run_id"],
                "bar_index": event["bar_index"],
                "recalc_iteration": recalc_iteration,
                "cause_sequence": broker_batch["sequence"],
                "broker_projection_hash": event["broker_projection_hash"],
                "broker_projection": event["broker_projection"],
            },
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        self._write_message(request)
        result = self._read_message()
        if result.get("kind") != "RECALC_RESULT":
            raise IsolatedWorkerError("worker did not return RECALC_RESULT")
        response = self._read_message()
        if response.get("kind") != "INTENT_BATCH":
            raise IsolatedWorkerError("worker did not return recalculated INTENT_BATCH")
        body = response.get("body")
        if not isinstance(body, dict):
            raise IsolatedWorkerError("worker recalculated INTENT_BATCH body is invalid")
        return body

    def commit_bar(self, event: Mapping[str, Any]) -> dict[str, Any]:
        state_ref = self._persist_artifact(event["state_artifact"])
        projection_ref = self._persist_artifact(
            event["broker_projection_artifact"]
        )
        body = {
            "run_id": event["run_id"],
            "bar_index": event["bar_index"],
            "recalc_iteration": event["recalc_iteration"],
            "state_hash": event["state_hash"],
            "broker_projection_hash": event["broker_projection_hash"],
            "state_ref": state_ref,
            "broker_projection_ref": projection_ref,
        }
        message = self.protocol.append(
            "BAR_COMMIT",
            body,
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        self._write_message(message)
        self._last_commit = message
        return message

    def heartbeat(self) -> None:
        if self._closed or self.proc.poll() is not None:
            raise IsolatedWorkerError("interactive worker heartbeat failed")

    def finalize(self) -> dict[str, Any]:
        if self._closed:
            return {"kind": "FINALIZE"}
        if self._last_commit is None:
            raise IsolatedWorkerError("cannot finalize without a committed bar")
        commit_body = self._last_commit["body"]
        message: dict[str, Any] | None = None
        try:
            message = self.protocol.append(
                "FINALIZE",
                {
                    "run_id": commit_body["run_id"],
                    "final_sequence": self._last_commit["sequence"],
                    "final_state_hash": commit_body["state_hash"],
                    "broker_projection_hash": commit_body["broker_projection_hash"],
                    "last_commit_message_id": self._last_commit["message_id"],
                    "last_committed_sequence": self._last_commit["sequence"],
                },
                created_at_utc_ms=int(self._last_commit["created_at_utc_ms"]),
            )
            self._write_message(message)
        finally:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._kill()
            finally:
                self._close_pipes()
                self._closed = True
        assert message is not None
        return message

    def __enter__(self) -> "InteractiveWorkerSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.finalize()
        else:
            try:
                abort = self.protocol.append(
                    "ABORT",
                    {
                        "run_id": self.protocol.execution_context["run_id"],
                        "error_code": "PARENT_ABORT",
                        "reason": str(exc or "isolated run aborted"),
                    },
                    created_at_utc_ms=0,
                )
                self._write_message(abort)
            except (IsolatedWorkerError, WorkerProtocolError, ValueError):
                pass
            finally:
                self._kill()


def evaluate_artifact(
    source: bytes,
    *,
    admitted_manifest: AdmittedManifest,
    instrument_id: str = "",
    timeout_s: float = 5.0,
    stack_id: str = "openpine-5.0",
    semantic_profile: str = "",
    cgroup_dir: str | Path | None = None,
    bars: list[dict[str, Any]] | None = None,
    htf_bars: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
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
            "instrument_id": instrument_id,
            "semantic_profile": semantic_profile,
            "bars": bars or [],
            "htf_bars": htf_bars or [],
            "params": {} if params is None else params,
        }
    )
    unit_name = _worker_unit_name()
    try:
        # Immutable argv: admitted bwrap + wheel-bound candidate Python. No shell or user path.
        proc = subprocess.Popen(  # noqa: S603
            _bwrap_argv(admitted_manifest, unit_name),
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
            try:
                _cleanup_worker_process(proc, unit_name)
            except IsolatedWorkerError as cleanup_exc:
                raise cleanup_exc from exc
            raise IsolatedWorkerError(str(exc)) from exc
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        try:
            _cleanup_worker_process(proc, unit_name)
        except IsolatedWorkerError as cleanup_exc:
            raise cleanup_exc from exc
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
