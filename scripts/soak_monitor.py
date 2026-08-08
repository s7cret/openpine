"""Unattended OpenPine operational soak monitor.

Records aggregate health only. It deliberately never writes strategy identifiers,
configuration, market rows, credentials, or response bodies.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_json(url: str, timeout: float) -> tuple[bool, float, Any, str | None]:
    started = time.perf_counter()
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
        return True, (time.perf_counter() - started) * 1000.0, payload, None
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return (
            False,
            (time.perf_counter() - started) * 1000.0,
            None,
            f"{type(exc).__name__}: {exc}",
        )


def service_pids(unit: str) -> list[int]:
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ControlGroup", "--value"],
        text=True,
        capture_output=True,
        check=False,
    )
    control_group = result.stdout.strip()
    procs = Path("/sys/fs/cgroup") / control_group.lstrip("/") / "cgroup.procs"
    if result.returncode == 0 and procs.is_file():
        return sorted({int(value) for value in procs.read_text().split() if value.isdigit()})
    fallback = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"],
        text=True,
        capture_output=True,
        check=False,
    )
    pid = fallback.stdout.strip()
    return [int(pid)] if fallback.returncode == 0 and pid.isdigit() and int(pid) else []


def process_memory(pids: list[int]) -> tuple[int, int]:
    rss_kib = 0
    swap_kib = 0
    for pid in pids:
        try:
            lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            if line.startswith("VmRSS:"):
                rss_kib += int(line.split()[1])
            elif line.startswith("VmSwap:"):
                swap_kib += int(line.split()[1])
    return rss_kib * 1024, swap_kib * 1024


def _worker(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    runtime = payload.get("runtime") or payload.get("runtime_health") or {}
    if not isinstance(runtime, dict):
        return {}
    worker = runtime.get("background_worker") or {}
    return worker if isinstance(worker, dict) else {}


def sample_once(
    *,
    base_url: str,
    unit: str,
    timeout: float,
    run_id: str,
    expected_source_tree_sha256: str,
    expected_stack_lock_sha256: str,
    config_sha256: str,
) -> dict[str, Any]:
    service = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit], check=False
    ).returncode == 0
    pids = service_pids(unit)
    rss_bytes, swap_bytes = process_memory(pids)
    health_ok, health_ms, health, health_error = request_json(
        f"{base_url}/health", timeout
    )
    dashboard_ok, dashboard_ms, dashboard, dashboard_error = request_json(
        f"{base_url}/api/dashboard", timeout
    )
    strategies_ok, strategies_ms, strategies, strategies_error = request_json(
        f"{base_url}/api/strategies", timeout
    )
    version_ok, version_ms, version, version_error = request_json(
        f"{base_url}/api/version", timeout
    )
    worker = _worker(health) or _worker(dashboard)
    rows = strategies if isinstance(strategies, list) else []
    if isinstance(strategies, dict):
        rows = strategies.get("items") or strategies.get("strategies") or []
    rows = rows if isinstance(rows, list) else []
    status_counts = Counter(
        str(row.get("status")) for row in rows if isinstance(row, dict)
    )
    version_payload_ok = version_ok and isinstance(version, dict)
    stack_lock = version.get("stack_lock") if version_payload_ok else None
    stack_lock = stack_lock if isinstance(stack_lock, dict) else {}
    modules = version.get("modules") if version_payload_ok else None
    modules = modules if isinstance(modules, list) else []
    openpine_module = next(
        (
            item
            for item in modules
            if isinstance(item, dict) and item.get("name") == "openpine"
        ),
        {},
    )
    runtime_identity_matches = bool(
        version_payload_ok
        and version.get("stack_conforms") is True
        and stack_lock.get("sha256") == expected_stack_lock_sha256
        and stack_lock.get("source_tree_sha256") == expected_source_tree_sha256
        and stack_lock.get("source_tree_matches") is True
        and openpine_module.get("lock_identity") == expected_source_tree_sha256
        and openpine_module.get("installed_identity") == expected_source_tree_sha256
        and openpine_module.get("conforms_to_lock") is True
    )
    return {
        "timestamp": utc_now(),
        "run_id": run_id,
        "expected_source_tree_sha256": expected_source_tree_sha256,
        "expected_stack_lock_sha256": expected_stack_lock_sha256,
        "config_sha256": config_sha256,
        "service_active": service,
        "process_count": len(pids),
        "rss_bytes": rss_bytes,
        "swap_bytes": swap_bytes,
        "health_ok": health_ok and isinstance(health, dict) and health.get("status") == "ok",
        "health_latency_ms": round(health_ms, 3),
        "dashboard_ok": dashboard_ok,
        "dashboard_latency_ms": round(dashboard_ms, 3),
        "strategies_ok": strategies_ok,
        "strategies_latency_ms": round(strategies_ms, 3),
        "runtime_version_ok": version_payload_ok,
        "runtime_version_latency_ms": round(version_ms, 3),
        "runtime_identity_matches": runtime_identity_matches,
        "worker_alive": bool(worker.get("alive")),
        "worker_ready": bool(worker.get("ready")),
        "worker_heartbeat_stale": bool(worker.get("heartbeat_stale")),
        "worker_degraded": bool(worker.get("degraded")),
        "worker_restart_count": int(worker.get("restart_count") or 0),
        "worker_reason": str(worker.get("reason") or "unknown"),
        "strategy_count": len(rows),
        "enabled_strategies": sum(
            bool(row.get("enabled")) for row in rows if isinstance(row, dict)
        ),
        "strategy_status_counts": dict(sorted(status_counts.items())),
        "last_bar_update": dashboard.get("last_bar_update")
        if isinstance(dashboard, dict)
        else None,
        "errors": [
            error
            for error in (health_error, dashboard_error, strategies_error, version_error)
            if error is not None
        ],
    }


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def summarize(
    samples: list[dict[str, Any]],
    *,
    max_worker_restarts: int,
    mode: str,
    memory_high_bytes: int,
    memory_max_bytes: int,
    max_swap_bytes: int,
    max_health_p95_ms: float,
    max_dashboard_p95_ms: float,
    min_progress_changes: int,
    completed: bool,
    expected_samples: int,
    expected_duration_seconds: float,
    elapsed_seconds: float,
    max_rss_growth_bytes: int,
    max_swap_growth_bytes: int,
    max_health_latency_ms: float,
    max_dashboard_latency_ms: float,
    run_id: str,
    expected_source_tree_sha256: str,
    expected_stack_lock_sha256: str,
    config_sha256: str,
) -> dict[str, Any]:
    if mode not in {"quiescent", "active"}:
        raise ValueError("mode must be 'quiescent' or 'active'")
    health_failures = sum(not bool(item.get("health_ok")) for item in samples)
    dashboard_failures = sum(not bool(item.get("dashboard_ok")) for item in samples)
    strategies_failures = sum(not bool(item.get("strategies_ok")) for item in samples)
    runtime_version_failures = sum(
        not bool(item.get("runtime_version_ok")) for item in samples
    )
    runtime_identity_mismatches = sum(
        not bool(item.get("runtime_identity_matches")) for item in samples
    )
    service_failures = sum(not bool(item.get("service_active")) for item in samples)
    worker_dead = sum(not bool(item.get("worker_alive")) for item in samples)
    worker_unready = sum(not bool(item.get("worker_ready")) for item in samples)
    worker_stale = sum(bool(item.get("worker_heartbeat_stale")) for item in samples)
    worker_degraded = sum(bool(item.get("worker_degraded")) for item in samples)
    enabled = sum(int(item.get("enabled_strategies") or 0) > 0 for item in samples)
    restart_count = max(
        (int(item.get("worker_restart_count") or 0) for item in samples), default=0
    )
    max_rss = max((int(item.get("rss_bytes") or 0) for item in samples), default=0)
    max_swap = max((int(item.get("swap_bytes") or 0) for item in samples), default=0)
    rss_growth = (
        int(samples[-1].get("rss_bytes") or 0)
        - int(samples[0].get("rss_bytes") or 0)
        if samples
        else 0
    )
    swap_growth = (
        int(samples[-1].get("swap_bytes") or 0)
        - int(samples[0].get("swap_bytes") or 0)
        if samples
        else 0
    )
    health_max = max(
        (float(item["health_latency_ms"]) for item in samples if item.get("health_ok")),
        default=None,
    )
    dashboard_max = max(
        (
            float(item["dashboard_latency_ms"])
            for item in samples
            if item.get("dashboard_ok")
        ),
        default=None,
    )
    health_p95 = _p95(
        [float(item["health_latency_ms"]) for item in samples if item.get("health_ok")]
    )
    dashboard_p95 = _p95(
        [
            float(item["dashboard_latency_ms"])
            for item in samples
            if item.get("dashboard_ok")
        ]
    )
    updates = [
        item.get("last_bar_update")
        for item in samples
        if item.get("last_bar_update") is not None
    ]
    progress_changes = max(0, len(dict.fromkeys(updates)) - 1)
    progress_requirement_met = mode == "quiescent" or (
        enabled == len(samples) and progress_changes >= min_progress_changes
    )
    unexpected_enabled = enabled if mode == "quiescent" else 0
    memory_high_exceeded = max_rss > memory_high_bytes
    memory_max_exceeded = max_rss > memory_max_bytes
    swap_limit_exceeded = max_swap > max_swap_bytes
    health_p95_exceeded = health_p95 is not None and health_p95 > max_health_p95_ms
    dashboard_p95_exceeded = (
        dashboard_p95 is not None and dashboard_p95 > max_dashboard_p95_ms
    )
    rss_growth_exceeded = rss_growth > max_rss_growth_bytes
    swap_growth_exceeded = swap_growth > max_swap_growth_bytes
    health_max_exceeded = health_max is not None and health_max > max_health_latency_ms
    dashboard_max_exceeded = (
        dashboard_max is not None and dashboard_max > max_dashboard_latency_ms
    )
    sample_requirement_met = len(samples) >= expected_samples
    duration_requirement_met = elapsed_seconds >= expected_duration_seconds
    complete = sample_requirement_met and duration_requirement_met
    failure = any(
        (
            not samples,
            health_failures,
            dashboard_failures,
            strategies_failures,
            runtime_version_failures,
            runtime_identity_mismatches,
            service_failures,
            worker_dead,
            worker_unready,
            worker_stale,
            worker_degraded,
            unexpected_enabled,
            restart_count > max_worker_restarts,
            memory_high_exceeded,
            memory_max_exceeded,
            swap_limit_exceeded,
            health_p95_exceeded,
            dashboard_p95_exceeded,
            rss_growth_exceeded,
            swap_growth_exceeded,
            health_max_exceeded,
            dashboard_max_exceeded,
            not progress_requirement_met,
        )
    )
    if failure:
        verdict = "fail"
    elif not completed:
        verdict = "running"
    elif not complete:
        verdict = "incomplete"
    else:
        verdict = "pass"
    return {
        "schema": "openpine.soak-summary.v2",
        "mode": mode,
        "identity": {
            "run_id": run_id,
            "expected_source_tree_sha256": expected_source_tree_sha256,
            "expected_stack_lock_sha256": expected_stack_lock_sha256,
            "config_sha256": config_sha256,
        },
        "verdict": verdict,
        "complete": complete,
        "sample_requirement_met": sample_requirement_met,
        "duration_requirement_met": duration_requirement_met,
        "expected_samples": expected_samples,
        "expected_duration_seconds": expected_duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "samples": len(samples),
        "started_at": samples[0].get("timestamp") if samples else None,
        "ended_at": samples[-1].get("timestamp") if samples else None,
        "service_failures": service_failures,
        "health_failures": health_failures,
        "dashboard_failures": dashboard_failures,
        "strategies_failures": strategies_failures,
        "runtime_version_failures": runtime_version_failures,
        "runtime_identity_mismatch_samples": runtime_identity_mismatches,
        "worker_dead_samples": worker_dead,
        "worker_unready_samples": worker_unready,
        "worker_stale_samples": worker_stale,
        "worker_degraded_samples": worker_degraded,
        "unexpected_enabled_strategy_samples": unexpected_enabled,
        "max_worker_restart_count": restart_count,
        "max_rss_bytes": max_rss,
        "max_swap_bytes": max_swap,
        "rss_growth_bytes": rss_growth,
        "swap_growth_bytes": swap_growth,
        "health_latency_p95_ms": health_p95,
        "dashboard_latency_p95_ms": dashboard_p95,
        "health_latency_max_ms": health_max,
        "dashboard_latency_max_ms": dashboard_max,
        "last_bar_update_changes": progress_changes,
        "progress_requirement_met": progress_requirement_met,
        "memory_high_exceeded": memory_high_exceeded,
        "memory_max_exceeded": memory_max_exceeded,
        "swap_limit_exceeded": swap_limit_exceeded,
        "health_p95_exceeded": health_p95_exceeded,
        "dashboard_p95_exceeded": dashboard_p95_exceeded,
        "rss_growth_exceeded": rss_growth_exceeded,
        "swap_growth_exceeded": swap_growth_exceeded,
        "health_max_exceeded": health_max_exceeded,
        "dashboard_max_exceeded": dashboard_max_exceeded,
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise argparse.ArgumentTypeError("expected a 64-character SHA-256 hex digest")
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-source-tree-sha256", type=_sha256, required=True)
    parser.add_argument("--expected-stack-lock-sha256", type=_sha256, required=True)
    parser.add_argument("--config-sha256", type=_sha256, required=True)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--unit", default="openpine-api.service")
    parser.add_argument("--max-worker-restarts", type=int, default=0)
    parser.add_argument("--mode", choices=("quiescent", "active"), default="quiescent")
    parser.add_argument("--memory-high-bytes", type=int, default=768 * 1024 * 1024)
    parser.add_argument("--memory-max-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--max-swap-bytes", type=int, default=0)
    parser.add_argument("--max-health-p95-ms", type=float, default=500.0)
    parser.add_argument("--max-dashboard-p95-ms", type=float, default=1000.0)
    parser.add_argument("--max-rss-growth-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--max-swap-growth-bytes", type=int, default=0)
    parser.add_argument("--max-health-latency-ms", type=float, default=2000.0)
    parser.add_argument("--max-dashboard-latency-ms", type=float, default=5000.0)
    parser.add_argument("--min-progress-changes", type=int, default=1)
    parser.add_argument("--samples", type=int, default=None)
    args = parser.parse_args(argv)
    if args.interval_seconds <= 0 or args.duration_hours <= 0:
        parser.error("duration and interval must be positive")
    expected_duration_seconds = args.duration_hours * 3600.0
    sample_count = args.samples or max(
        2, math.ceil(expected_duration_seconds / args.interval_seconds) + 1
    )
    summary_options = {
        "max_worker_restarts": args.max_worker_restarts,
        "mode": args.mode,
        "memory_high_bytes": args.memory_high_bytes,
        "memory_max_bytes": args.memory_max_bytes,
        "max_swap_bytes": args.max_swap_bytes,
        "max_health_p95_ms": args.max_health_p95_ms,
        "max_dashboard_p95_ms": args.max_dashboard_p95_ms,
        "min_progress_changes": args.min_progress_changes,
        "expected_samples": sample_count,
        "expected_duration_seconds": expected_duration_seconds,
        "max_rss_growth_bytes": args.max_rss_growth_bytes,
        "max_swap_growth_bytes": args.max_swap_growth_bytes,
        "max_health_latency_ms": args.max_health_latency_ms,
        "max_dashboard_latency_ms": args.max_dashboard_latency_ms,
        "run_id": args.run_id,
        "expected_source_tree_sha256": args.expected_source_tree_sha256,
        "expected_stack_lock_sha256": args.expected_stack_lock_sha256,
        "config_sha256": args.config_sha256,
    }
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("output directory must be empty")
    samples_path = output / "samples.jsonl"
    samples: list[dict[str, Any]] = []
    started = time.monotonic()
    deadline = time.monotonic()
    with samples_path.open("x", encoding="utf-8") as stream:
        for index in range(sample_count):
            item = sample_once(
                base_url=args.base_url.rstrip("/"),
                unit=args.unit,
                timeout=args.request_timeout,
                run_id=args.run_id,
                expected_source_tree_sha256=args.expected_source_tree_sha256,
                expected_stack_lock_sha256=args.expected_stack_lock_sha256,
                config_sha256=args.config_sha256,
            )
            samples.append(item)
            stream.write(json.dumps(item, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            _atomic_json(
                output / "summary.json",
                summarize(
                    samples,
                    completed=False,
                    elapsed_seconds=time.monotonic() - started,
                    **summary_options,
                ),
            )
            if index + 1 < sample_count:
                deadline += args.interval_seconds
                time.sleep(max(0.0, deadline - time.monotonic()))
    summary = summarize(
        samples,
        completed=True,
        elapsed_seconds=time.monotonic() - started,
        **summary_options,
    )
    _atomic_json(output / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
