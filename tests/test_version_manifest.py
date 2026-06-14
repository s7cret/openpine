"""Tests for the GET /api/version manifest route."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openpine.gateway.routes import version


def _client(state) -> TestClient:
    from openpine.gateway.deps import get_state

    app = FastAPI()
    app.include_router(version.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    return TestClient(app)


def test_version_manifest_returns_tracked_modules_and_runtime() -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir="/tmp"))
    response = _client(state).get("/api/version")
    assert response.status_code == 200
    payload = response.json()

    # Stable response shape
    assert set(payload.keys()) == {"modules", "runtime"}
    assert isinstance(payload["modules"], list)
    assert len(payload["modules"]) == 7

    names = [m["name"] for m in payload["modules"]]
    assert names == [
        "openpine",
        "pine2ast",
        "ast2python",
        "pinelib",
        "marketdata_provider",
        "backtest_engine",
        "optimizer",
    ]

    # Every entry has the full schema, even when not installed
    for entry in payload["modules"]:
        assert set(entry.keys()) == {"name", "version", "installed", "path", "summary"}

    # openpine is the workspace checkout and is definitely installed here
    openpine = next(m for m in payload["modules"] if m["name"] == "openpine")
    assert openpine["installed"] is True
    assert openpine["version"] is not None
    assert openpine["path"] is not None
    assert openpine["path"].endswith("/openpine/__init__.py")
    assert openpine["summary"] is not None

    # Runtime info is complete
    runtime = payload["runtime"]
    assert {"python", "platform", "machine", "node"} <= set(runtime.keys())
    assert runtime["python"] is not None
    assert runtime["platform"] is not None


def test_version_manifest_reports_missing_module(tmp_path, monkeypatch) -> None:
    """When a tracked module cannot be found, the entry stays present with nulls."""
    from openpine.gateway.routes import version as version_mod

    state = SimpleNamespace(config=SimpleNamespace(data_dir=str(tmp_path)))
    client = _client(state)

    # Monkeypatch _TRACKED_MODULES to include a name we know is missing
    sentinel = "definitely_not_a_real_module_xyz_999"
    monkeypatch.setattr(version_mod, "_TRACKED_MODULES", ("openpine", sentinel))

    response = client.get("/api/version")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["modules"]) == 2
    missing = payload["modules"][1]
    assert missing["name"] == sentinel
    assert missing["installed"] is False
    assert missing["version"] is None
    assert missing["path"] is None
    assert missing["summary"] is None


def test_module_origin_uses_find_spec(monkeypatch) -> None:
    """_module_origin returns the spec origin for a normal package."""
    origin = version._module_origin("openpine")
    assert origin is not None
    assert origin.endswith("/openpine/__init__.py")


def test_module_origin_returns_none_for_missing() -> None:
    assert version._module_origin("definitely_not_a_real_module_xyz_999") is None
