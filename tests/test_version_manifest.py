"""Tests for the GET /api/version manifest route."""

from __future__ import annotations

from types import SimpleNamespace

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
    assert set(payload.keys()) == {"modules", "runtime", "stack_lock", "stack_conforms"}
    assert isinstance(payload["modules"], list)
    assert len(payload["modules"]) == 7
    assert payload["stack_lock"]["schema"] == "openpine.stack-lock.v1"
    assert payload["stack_lock"]["release"] == "4.0.1"
    assert len(payload["stack_lock"]["sha256"]) == 64
    assert len(payload["stack_lock"]["components"]) == 7

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
        assert set(entry.keys()) == {
            "name",
            "version",
            "module_version",
            "distribution_version",
            "lock_version",
            "lock_identity",
            "installed_identity",
            "identity_conforms",
            "conforms_to_lock",
            "installed",
            "path",
            "summary",
        }

    # openpine is the workspace checkout and is definitely installed here
    openpine = next(m for m in payload["modules"] if m["name"] == "openpine")
    assert openpine["installed"] is True
    assert openpine["version"] == "4.0.1"
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


def test_module_record_prefers_distribution_metadata_and_exposes_runtime_drift(monkeypatch) -> None:
    monkeypatch.setattr(version, "_module_origin", lambda _name: "/tmp/pkg/__init__.py")
    monkeypatch.setattr(version, "_runtime_version", lambda _name: "4.0.1-dirty")
    monkeypatch.setattr(version.importlib.metadata, "version", lambda _name: "4.0.0")
    monkeypatch.setattr(version, "_module_summary", lambda _name: "summary")

    record = version._module_record("pine2ast", lock_version="4.0.1")

    assert record["version"] == "4.0.0"
    assert record["distribution_version"] == "4.0.0"
    assert record["module_version"] == "4.0.1-dirty"
    assert record["lock_version"] == "4.0.1"
    assert record["conforms_to_lock"] is False


def test_module_record_requires_exact_vcs_identity_for_sibling_conformance(monkeypatch) -> None:
    commit = "a" * 40
    monkeypatch.setattr(version, "_module_origin", lambda _name: "/tmp/pkg/__init__.py")
    monkeypatch.setattr(version, "_runtime_version", lambda _name: "4.0.1")
    monkeypatch.setattr(version.importlib.metadata, "version", lambda _name: "4.0.1")
    monkeypatch.setattr(version, "_module_summary", lambda _name: "summary")
    monkeypatch.setattr(version, "_distribution_vcs_commit", lambda _name: commit)

    matching = version._module_record(
        "pine2ast", lock_version="4.0.1", lock_commit=commit
    )
    mismatched = version._module_record(
        "pine2ast", lock_version="4.0.1", lock_commit="b" * 40
    )

    assert matching["identity_conforms"] is True
    assert matching["conforms_to_lock"] is True
    assert mismatched["identity_conforms"] is False
    assert mismatched["conforms_to_lock"] is False


def test_module_record_prefers_exact_tree_identity_for_wheel_conformance(monkeypatch) -> None:
    tree_sha256 = "d" * 64
    monkeypatch.setattr(version, "_module_origin", lambda _name: "/tmp/pkg/__init__.py")
    monkeypatch.setattr(version, "_runtime_version", lambda _name: "4.0.1")
    monkeypatch.setattr(version.importlib.metadata, "version", lambda _name: "4.0.1")
    monkeypatch.setattr(version, "_module_summary", lambda _name: "summary")
    monkeypatch.setattr(version, "package_tree_identity", lambda _path: tree_sha256)

    def unexpected_vcs_lookup(_name: str) -> str:
        raise AssertionError("wheel tree identity must not fall back to VCS metadata")

    monkeypatch.setattr(version, "_distribution_vcs_commit", unexpected_vcs_lookup)

    record = version._module_record(
        "pine2ast",
        lock_version="4.0.1",
        lock_commit="a" * 40,
        lock_tree_sha256=tree_sha256,
    )

    assert record["lock_identity"] == tree_sha256
    assert record["installed_identity"] == tree_sha256
    assert record["identity_conforms"] is True
    assert record["conforms_to_lock"] is True


def test_distribution_vcs_commit_reads_direct_url_metadata(monkeypatch) -> None:
    class Distribution:
        def read_text(self, filename: str) -> str | None:
            assert filename == "direct_url.json"
            return '{"vcs_info":{"commit_id":"' + ("c" * 40) + '"}}'

    monkeypatch.setattr(version.importlib.metadata, "distribution", lambda _name: Distribution())

    assert version._distribution_vcs_commit("pine2ast") == "c" * 40


def test_version_manifest_rejects_valid_versions_when_lock_tree_identity_is_wrong(
    monkeypatch,
) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir="/tmp"))
    components = [
        {"name": name, "version": "4.0.1", "commit": "a" * 40}
        for name in version._TRACKED_MODULES
    ]
    components[0] = {
        "name": "openpine",
        "version": "4.0.1",
        "tree_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        version,
        "stack_lock_summary",
        lambda: {
            "schema": "openpine.stack-lock.v1",
            "release": "4.0.1",
            "sha256": "d" * 64,
            "source_tree_matches": False,
            "components": components,
        },
    )
    monkeypatch.setattr(
        version,
        "_module_record",
        lambda name, **_kwargs: {"name": name, "conforms_to_lock": True},
    )

    payload = _client(state).get("/api/version").json()

    assert payload["stack_conforms"] is False


def test_module_origin_uses_find_spec(monkeypatch) -> None:
    """_module_origin returns the spec origin for a normal package."""
    origin = version._module_origin("openpine")
    assert origin is not None
    assert origin.endswith("/openpine/__init__.py")


def test_module_origin_returns_none_for_missing() -> None:
    assert version._module_origin("definitely_not_a_real_module_xyz_999") is None
