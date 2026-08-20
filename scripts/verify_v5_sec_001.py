#!/usr/bin/env python3
"""Execute the V5-SEC-001 isolated-worker acceptance contract."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from openpine.runtime.engine import BacktestArtifactError, _load_generated_module
from openpine.runtime.isolated_worker import (
    TMPFS_BYTES,
    IsolatedWorkerError,
    evaluate_artifact,
    worker_user_uid,
)

SCHEMA = "openpine.v5-sec-001.acceptance.v1"
EXPECTED_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PWD",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "TZ",
}
CHECK_NAMES = (
    "dedicated_worker_user",
    "network_namespace",
    "pid_namespace",
    "read_only_root",
    "read_only_runtime",
    "quota_scratch",
    "host_filesystem_hidden",
    "clear_environment",
    "resource_limits",
    "canonical_intent_tape",
    "captured_source_bytes",
    "in_process_loader_forbidden",
    "parent_import_isolation",
)


def _source(sentinel: Path) -> bytes:
    return textwrap.dedent(
        f"""
        import os
        from pinelib.strategy.context import StrategyContext

        CAPTURED_MARKER = "original"

        def writable(path):
            try:
                open(path, "w").write("x")
                return True
            except OSError:
                return False

        ROOT_WRITABLE = writable("/.openpine-v5-sec-001")
        TRUSTED_WRITABLE = writable("/tmp/openpine-trusted/.openpine-v5-sec-001")
        SCRATCH_WRITABLE = writable("/tmp/.openpine-v5-sec-001")
        stat = os.statvfs("/tmp")
        SCRATCH_BYTES = stat.f_frsize * stat.f_blocks
        ETC_VISIBLE = os.path.exists("/etc/passwd")
        VAR_VISIBLE = os.path.exists("/var/lib")
        HOST_SENTINEL_VISIBLE = os.path.exists({str(sentinel)!r})
        VISIBLE_PIDS = sorted(int(item) for item in os.listdir("/proc") if item.isdigit())

        ctx = StrategyContext(intent_run_id="v5-sec-001", intent_strategy_id="acceptance")
        ctx.entry("L", "long", qty=1)
        """
    ).encode("utf-8")


def _loader_is_forbidden() -> bool:
    try:
        _load_generated_module(Path("/nonexistent"), "sec", "001")
    except BacktestArtifactError as exc:
        return "in-process generated import is forbidden" in str(exc)
    return False


def _empty_report(worker_uid: int | None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "passed": False,
        "worker_uid": worker_uid,
        "checks": {name: False for name in CHECK_NAMES},
        "evidence": {},
    }


def build_report() -> dict[str, Any]:
    worker_uid = worker_user_uid()
    report = _empty_report(worker_uid)
    if worker_uid is None:
        report["error"] = "dedicated openpine-worker user is unavailable"
        return report

    with NamedTemporaryFile(prefix="openpine-v5-sec-001-", delete=False) as handle:
        sentinel = Path(handle.name)
        handle.write(b"host-only")
    with NamedTemporaryFile(prefix="openpine-v5-artifact-", delete=False) as handle:
        artifact_path = Path(handle.name)
        handle.write(_source(sentinel))
    try:
        captured_source = artifact_path.read_bytes()
        artifact_path.write_bytes(b'CAPTURED_MARKER = "mutated"\n')
        modules_before = set(sys.modules)
        result = evaluate_artifact(
            captured_source,
            semantic_profile="strict_5x",
            timeout_s=8,
        )
    except IsolatedWorkerError as exc:
        report["error"] = str(exc)
        return report
    finally:
        sentinel.unlink(missing_ok=True)
        artifact_path.unlink(missing_ok=True)

    namespace = result.get("namespace") or {}
    isolation = result.get("isolation") or {}
    tape = result.get("intent_tape") or []
    pids = namespace.get("VISIBLE_PIDS") or []
    limits = isolation.get("rlimits") or {}
    event = tape[0] if len(tape) == 1 else {}
    generated_modules = sorted(
        name
        for name in set(sys.modules) - modules_before
        if name.startswith("openpine_generated_")
    )

    checks = {
        "dedicated_worker_user": isolation.get("uid") == worker_uid and worker_uid > 0,
        "network_namespace": isolation.get("network") == "blocked",
        "pid_namespace": 1 in pids and len(pids) <= 2 and os.getpid() not in pids,
        "read_only_root": namespace.get("ROOT_WRITABLE") is False,
        "read_only_runtime": (
            isolation.get("usr_writable") is False
            and namespace.get("TRUSTED_WRITABLE") is False
        ),
        "quota_scratch": (
            namespace.get("SCRATCH_WRITABLE") is True
            and 0 < int(namespace.get("SCRATCH_BYTES") or 0) <= TMPFS_BYTES
        ),
        "host_filesystem_hidden": (
            isolation.get("home_visible") is False
            and namespace.get("ETC_VISIBLE") is False
            and namespace.get("VAR_VISIBLE") is False
            and namespace.get("HOST_SENTINEL_VISIBLE") is False
        ),
        "clear_environment": set(isolation.get("env") or []) == EXPECTED_ENV,
        "resource_limits": limits
        == {
            "address_space": [134_217_728, 134_217_728],
            "cpu": [2, 2],
            "file_size": [1_048_576, 1_048_576],
            "processes": [32, 32],
        },
        "canonical_intent_tape": (
            event.get("schema_id") == "openpine.intent.v2"
            and event.get("kind") == "entry"
            and event.get("origin_command_kind") == "entry.long"
            and bool(event.get("content_hash"))
        ),
        "captured_source_bytes": namespace.get("CAPTURED_MARKER") == "original",
        "in_process_loader_forbidden": _loader_is_forbidden(),
        "parent_import_isolation": not generated_modules,
    }
    report["checks"] = checks
    report["evidence"] = {
        "environment": isolation.get("env"),
        "network": isolation.get("network"),
        "rlimits": limits,
        "scratch_bytes": namespace.get("SCRATCH_BYTES"),
        "visible_pids": pids,
        "generated_parent_modules": generated_modules,
    }
    report["passed"] = all(checks.values())
    return report


def main() -> int:
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
