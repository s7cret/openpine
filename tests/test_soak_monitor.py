from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE_TREE_SHA256 = "a" * 64
EXPECTED_STACK_LOCK_SHA256 = "b" * 64
CONFIG_SHA256 = "c" * 64
RUN_ID = "soak-review-001"


def _module():
    path = ROOT / "scripts" / "soak_monitor.py"
    spec = importlib.util.spec_from_file_location("soak_monitor", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _healthy(**overrides):
    sample = {
        "service_active": True,
        "rss_bytes": 100,
        "swap_bytes": 0,
        "health_ok": True,
        "health_latency_ms": 10.0,
        "dashboard_ok": True,
        "dashboard_latency_ms": 20.0,
        "strategies_ok": True,
        "runtime_version_ok": True,
        "runtime_identity_matches": True,
        "worker_alive": True,
        "worker_ready": True,
        "worker_heartbeat_stale": False,
        "worker_degraded": False,
        "worker_restart_count": 0,
        "enabled_strategies": 0,
        "last_bar_update": None,
    }
    sample.update(overrides)
    return sample


def _summary(monitor, samples, **overrides):
    kwargs = {
        "max_worker_restarts": 0,
        "mode": "quiescent",
        "memory_high_bytes": 200,
        "memory_max_bytes": 300,
        "max_swap_bytes": 0,
        "max_health_p95_ms": 50,
        "max_dashboard_p95_ms": 50,
        "min_progress_changes": 0,
        "completed": True,
        "expected_samples": len(samples),
        "expected_duration_seconds": 0.0,
        "elapsed_seconds": 0.0,
        "max_rss_growth_bytes": 100,
        "max_swap_growth_bytes": 0,
        "max_health_latency_ms": 100,
        "max_dashboard_latency_ms": 100,
        "run_id": RUN_ID,
        "expected_source_tree_sha256": EXPECTED_SOURCE_TREE_SHA256,
        "expected_stack_lock_sha256": EXPECTED_STACK_LOCK_SHA256,
        "config_sha256": CONFIG_SHA256,
    }
    kwargs.update(overrides)
    return monitor.summarize(samples, **kwargs)


def _runtime_version_payload(
    *,
    source_tree_sha256: str = EXPECTED_SOURCE_TREE_SHA256,
    stack_lock_sha256: str = EXPECTED_STACK_LOCK_SHA256,
) -> dict:
    return {
        "stack_lock": {
            "sha256": stack_lock_sha256,
            "source_tree_sha256": source_tree_sha256,
            "source_tree_matches": True,
        },
        "modules": [
            {
                "name": "openpine",
                "version": "4.0.1",
                "lock_identity": EXPECTED_SOURCE_TREE_SHA256,
                "installed_identity": source_tree_sha256,
                "conforms_to_lock": True,
            }
        ],
        "stack_conforms": True,
    }


def _sample_once(monitor, monkeypatch, *, version_ok=True, version_payload=None):
    health = {
        "status": "ok",
        "runtime": {
            "background_worker": {
                "alive": True,
                "ready": True,
                "heartbeat_stale": False,
                "degraded": False,
                "restart_count": 0,
            }
        },
    }
    responses = {
        "/health": (True, 10.0, health, None),
        "/api/dashboard": (True, 20.0, {}, None),
        "/api/strategies": (True, 30.0, [], None),
        "/api/version": (
            version_ok,
            40.0,
            version_payload,
            None if version_ok else "URLError: unavailable",
        ),
    }
    monkeypatch.setattr(
        monitor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(monitor, "service_pids", lambda _unit: [123])
    monkeypatch.setattr(monitor, "process_memory", lambda _pids: (100, 0))
    monkeypatch.setattr(
        monitor,
        "request_json",
        lambda url, _timeout: next(
            response for suffix, response in responses.items() if url.endswith(suffix)
        ),
    )
    return monitor.sample_once(
        base_url="http://127.0.0.1:8080",
        unit="openpine-api.service",
        timeout=1.0,
        run_id=RUN_ID,
        expected_source_tree_sha256=EXPECTED_SOURCE_TREE_SHA256,
        expected_stack_lock_sha256=EXPECTED_STACK_LOCK_SHA256,
        config_sha256=CONFIG_SHA256,
    )


def test_soak_sample_missing_worker_ready_is_unready(monkeypatch) -> None:
    monitor = _module()
    version = _runtime_version_payload()
    sample = _sample_once(monitor, monkeypatch, version_payload=version)

    health = {
        "status": "ok",
        "runtime": {
            "background_worker": {
                "alive": True,
                "heartbeat_stale": False,
                "degraded": False,
            }
        },
    }
    original_request = monitor.request_json
    monkeypatch.setattr(
        monitor,
        "request_json",
        lambda url, timeout: (True, 10.0, health, None)
        if url.endswith("/health")
        else original_request(url, timeout),
    )

    sample = monitor.sample_once(
        base_url="http://127.0.0.1:8080",
        unit="openpine-api.service",
        timeout=1.0,
        run_id=RUN_ID,
        expected_source_tree_sha256=EXPECTED_SOURCE_TREE_SHA256,
        expected_stack_lock_sha256=EXPECTED_STACK_LOCK_SHA256,
        config_sha256=CONFIG_SHA256,
    )

    assert sample["worker_alive"] is True
    assert sample["worker_ready"] is False
    assert _summary(monitor, [sample])["verdict"] == "fail"


def test_soak_runtime_identity_mismatch_fails_sample_and_summary(monkeypatch) -> None:
    monitor = _module()
    sample = _sample_once(
        monitor,
        monkeypatch,
        version_payload=_runtime_version_payload(source_tree_sha256="d" * 64),
    )

    assert sample["runtime_version_ok"] is True
    assert sample["runtime_identity_matches"] is False
    summary = _summary(monitor, [sample])
    assert summary["verdict"] == "fail"
    assert summary["runtime_identity_mismatch_samples"] == 1
    assert summary["identity"] == {
        "run_id": RUN_ID,
        "expected_source_tree_sha256": EXPECTED_SOURCE_TREE_SHA256,
        "expected_stack_lock_sha256": EXPECTED_STACK_LOCK_SHA256,
        "config_sha256": CONFIG_SHA256,
    }


def test_soak_missing_runtime_version_fails_sample_and_summary(monkeypatch) -> None:
    monitor = _module()
    sample = _sample_once(
        monitor,
        monkeypatch,
        version_ok=False,
        version_payload=None,
    )

    assert sample["runtime_version_ok"] is False
    assert sample["runtime_identity_matches"] is False
    summary = _summary(monitor, [sample])
    assert summary["verdict"] == "fail"
    assert summary["runtime_version_failures"] == 1


def test_soak_summary_distinguishes_healthy_and_degraded_samples() -> None:
    monitor = _module()
    healthy = _healthy()
    summary = _summary(monitor, [healthy, healthy])
    assert summary["verdict"] == "pass"
    assert summary["samples"] == 2
    assert summary["max_rss_bytes"] == 100

    failed = _healthy(
        health_ok=False,
        worker_alive=False,
        worker_ready=False,
        worker_heartbeat_stale=True,
        worker_degraded=True,
        worker_restart_count=1,
        enabled_strategies=1,
    )
    summary = _summary(monitor, [healthy, failed])
    assert summary["verdict"] == "fail"
    assert summary["health_failures"] == 1
    assert summary["worker_dead_samples"] == 1
    assert summary["worker_unready_samples"] == 1
    assert summary["worker_stale_samples"] == 1
    assert summary["unexpected_enabled_strategy_samples"] == 1
    assert summary["max_worker_restart_count"] == 1


def test_soak_summary_enforces_memory_swap_and_latency_limits() -> None:
    monitor = _module()
    samples = [
        _healthy(rss_bytes=90),
        _healthy(
            rss_bytes=110,
            swap_bytes=2,
            health_latency_ms=60.0,
            dashboard_latency_ms=70.0,
        ),
    ]

    summary = _summary(
        monitor,
        samples,
        memory_high_bytes=100,
        memory_max_bytes=105,
    )

    assert summary["verdict"] == "fail"
    assert summary["memory_high_exceeded"] is True
    assert summary["memory_max_exceeded"] is True
    assert summary["swap_limit_exceeded"] is True
    assert summary["health_p95_exceeded"] is True
    assert summary["dashboard_p95_exceeded"] is True


def test_soak_active_mode_requires_enabled_strategy_and_progress() -> None:
    monitor = _module()
    progressing = [
        _healthy(enabled_strategies=1, last_bar_update=1000),
        _healthy(enabled_strategies=1, last_bar_update=2000),
    ]

    summary = _summary(
        monitor,
        progressing,
        mode="active",
        memory_high_bytes=200,
        memory_max_bytes=300,
        min_progress_changes=1,
    )
    assert summary["verdict"] == "pass"
    assert summary["progress_requirement_met"] is True

    stalled = [
        _healthy(enabled_strategies=1, last_bar_update=1000),
        _healthy(enabled_strategies=1, last_bar_update=1000),
    ]
    summary = _summary(
        monitor,
        stalled,
        mode="active",
        memory_high_bytes=200,
        memory_max_bytes=300,
        min_progress_changes=1,
    )
    assert summary["verdict"] == "fail"
    assert summary["progress_requirement_met"] is False


def test_soak_quiescent_mode_rejects_active_strategies_without_requiring_progress() -> None:
    monitor = _module()
    summary = _summary(monitor, [_healthy(enabled_strategies=1)])
    assert summary["verdict"] == "fail"
    assert summary["mode"] == "quiescent"
    assert summary["progress_requirement_met"] is True


def test_soak_partial_and_short_terminal_runs_never_pass() -> None:
    monitor = _module()
    running = _summary(
        monitor,
        [_healthy()],
        completed=False,
        expected_samples=1441,
        expected_duration_seconds=86_400.0,
        elapsed_seconds=0.0,
    )
    assert running["verdict"] == "running"
    assert running["complete"] is False

    terminal = _summary(
        monitor,
        [_healthy()],
        completed=True,
        expected_samples=1441,
        expected_duration_seconds=86_400.0,
        elapsed_seconds=0.0,
    )
    assert terminal["verdict"] == "incomplete"
    assert terminal["sample_requirement_met"] is False
    assert terminal["duration_requirement_met"] is False


def test_soak_gates_growth_and_single_latency_spikes() -> None:
    monitor = _module()
    samples = [_healthy(rss_bytes=100, swap_bytes=0) for _ in range(19)]
    samples.append(
        _healthy(
            rss_bytes=180,
            swap_bytes=10,
            health_latency_ms=500.0,
            dashboard_latency_ms=600.0,
        )
    )

    summary = _summary(
        monitor,
        samples,
        memory_high_bytes=1_000,
        memory_max_bytes=2_000,
        max_swap_bytes=100,
        max_health_p95_ms=100.0,
        max_dashboard_p95_ms=100.0,
        max_rss_growth_bytes=50,
        max_swap_growth_bytes=5,
        max_health_latency_ms=200.0,
        max_dashboard_latency_ms=300.0,
    )

    assert summary["verdict"] == "fail"
    assert summary["rss_growth_exceeded"] is True
    assert summary["swap_growth_exceeded"] is True
    assert summary["health_max_exceeded"] is True
    assert summary["dashboard_max_exceeded"] is True
    assert summary["health_p95_exceeded"] is False
    assert summary["dashboard_p95_exceeded"] is False


def test_soak_rejects_reused_output_directory(tmp_path: Path) -> None:
    monitor = _module()
    output = tmp_path / "reused"
    output.mkdir()
    (output / "samples.jsonl").write_text('{"stale": true}\n')

    with pytest.raises(SystemExit) as error:
        monitor.main(
            [
                "--output-dir",
                str(output),
                "--duration-hours",
                "0.001",
                "--interval-seconds",
                "1",
                "--samples",
                "2",
            ]
        )

    assert error.value.code == 2
