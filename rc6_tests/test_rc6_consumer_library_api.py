"""Standard CLI and HTTP input paths; real source/artifact SQLite stores."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from openpine.artifacts.store import ArtifactStore
from openpine.gateway.deps import get_state
from openpine.gateway.routes import pine_ops
from openpine.jobs.persist import JobV1Store
from openpine.pine.registry import SQLitePineSourceRegistry
from rc6_tests.test_rc6_consumer_library_project import COMMITS, payload, root, write_project
from tests.admission_helpers import make_deployment_identity


@pytest.fixture
def state(tmp_path, monkeypatch):
    registry = SQLitePineSourceRegistry(tmp_path / "pine.sqlite")
    source = registry.add_source(root().source_text, "consumer")
    jobs = JobV1Store(tmp_path / "jobs.sqlite")
    state = SimpleNamespace(
        pine_registry=registry,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        job_store=jobs,
        admission_identity=make_deployment_identity(),
        source=source,
    )
    monkeypatch.setattr("openpine.build_identity.compiler_producer_commits", lambda: dict(COMMITS))
    yield state
    registry.close()
    close = getattr(jobs, "close", None)
    if close:
        close()


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(pine_ops.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("route", ["validate", "compile"])
def test_http_inline_project_uses_standard_endpoint_and_original_sources(client, state, route):
    response = client.post(f"/api/pine/{state.source.id}/{route}", json={"libraries": payload()})
    assert response.status_code == 200, response.text
    body = response.json()
    if route == "validate":
        assert body["valid"] and body["diagnostics"] == []
        assert state.pine_registry.get_source(state.source.id).active_artifact_id is None
        assert state.artifact_store.list_artifacts(state.source.id) == []
    else:
        progress = client.get("/api/pine/compile/progress/" + body["operation_id"]).json()
        assert progress["status"] == "completed", progress
        aid = progress["detail"]["artifact_id"]
        assert state.pine_registry.get_source(state.source.id).active_artifact_id == aid
        loaded = state.artifact_store.get_artifact(aid, state.source.id)
        assert loaded["source_text"] == state.source.source_text
        inspected = client.get(f"/api/pine/{state.source.id}/artifacts/{aid}")
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()["original_source_projection"]["entries"]


@pytest.mark.parametrize("route", ["validate", "compile"])
def test_old_http_clients_without_body_still_work_for_scripts_without_imports(client, state, route):
    src = state.pine_registry.add_source('//@version=6\nstrategy("plain")\n', "plain")
    response = client.post(f"/api/pine/{src.id}/{route}")
    assert response.status_code == 200, response.text
    data = response.json()
    if route == "validate":
        assert data["valid"]
    else:
        assert (
            client.get("/api/pine/compile/progress/" + data["operation_id"]).json()["status"]
            == "completed"
        )


@pytest.mark.parametrize("route", ["validate", "compile"])
@pytest.mark.parametrize(
    "bad", ["server_path", "hash", "source_hash", "extra", "missing_hash", "coerce"]
)
def test_http_rejects_untrusted_library_input_before_scheduling(client, state, route, bad):
    data = {"libraries": payload()}
    if bad == "server_path":
        data = {"library_lock_path": "/etc/passwd"}
    elif bad == "hash":
        data["libraries"]["expected_lock_hash"] = "sha256:" + "c" * 64
    elif bad == "source_hash":
        data["libraries"]["sources"]["qa/Base/1"] += "// changed"
    elif bad == "extra":
        data["libraries"]["sources"]["qa/Unexpected/1"] = "unlocked"
    elif bad == "missing_hash":
        del data["libraries"]["expected_lock_hash"]
    else:
        data["libraries"]["sources"]["qa/Base/1"] = 17
    response = client.post(f"/api/pine/{state.source.id}/{route}", json=data)
    assert response.status_code in (400, 422), response.text
    assert state.artifact_store.list_artifacts(state.source.id) == []
    assert state.pine_registry.get_source(state.source.id).active_artifact_id is None


@pytest.mark.parametrize("route", ["validate", "compile"])
def test_http_failure_keeps_original_diagnostic_and_previous_active_artifact(client, state, route):
    state.pine_registry.set_active_artifact(state.source.id, "previous")
    data = payload()
    data["sources"]["qa/Base/1"] = (
        '//@version=6\nlibrary("Base")\n// expected location\nexport step(float x)=>x+"bad"\n'
    )
    from pine2ast.libraries import LibraryStore

    store = LibraryStore.create(data["sources"])
    data["lock"] = store.lock()
    data["expected_lock_hash"] = store.content_hash
    response = client.post(f"/api/pine/{state.source.id}/{route}", json={"libraries": data})
    assert response.status_code == 200, response.text
    if route == "validate":
        result = response.json()
        assert not result["valid"]
        rows = result["diagnostics"]
    else:
        progress = client.get(
            "/api/pine/compile/progress/" + response.json()["operation_id"]
        ).json()
        assert progress["status"] == "failed", progress
        rows = progress["detail"]["diagnostics"]
        loaded = state.artifact_store.get_artifact(
            progress["detail"]["artifact_id"], state.source.id
        )
        assert loaded["compile_meta"]["diagnostics"] == rows and loaded["python_code"] == ""
    assert any(
        d["location"] and d["location"]["source"] == "qa/Base/1" and d["location"]["line"] == 4
        for d in rows
    ), rows
    assert state.pine_registry.get_source(state.source.id).active_artifact_id == "previous"


def test_queued_source_and_libraries_are_snapshots_and_compile_runs_off_event_loop(
    state, monkeypatch
):
    async def scenario():
        tasks = BackgroundTasks()
        data = pine_ops.PineCompileRequest(libraries=pine_ops.LibraryInputs(**payload()))
        original_source = state.source.source_text
        actual = pine_ops._compile_native_rc6
        calls = []
        main_thread = threading.get_ident()

        def compile_spy(source, **kwargs):
            calls.append(
                (
                    threading.get_ident(),
                    source.source_text,
                    kwargs["library_store"].source("qa/Base/1"),
                )
            )
            return actual(source, **kwargs)

        monkeypatch.setattr(pine_ops, "_compile_native_rc6", compile_spy)
        answer = await pine_ops.compile_pine(state.source.id, tasks, state, body=data)
        data.libraries.sources.clear()
        state.source.source_text = '//@version=6\nstrategy("edited")\n'
        await tasks()
        progress = pine_ops.ws_manager.get_progress(answer["operation_id"])
        assert progress["status"] == "completed", progress
        assert calls[0][0] != main_thread and calls[0][1] == original_source
        assert "x+1" in calls[0][2]
        assert state.pine_registry.get_source(state.source.id).active_artifact_id is None
        assert progress["detail"]["activated"] is False

    asyncio.run(scenario())


def test_operation_ids_are_unique_and_compile_rejects_missing_admission(state):
    async def scenario():
        data = pine_ops.PineCompileRequest(libraries=pine_ops.LibraryInputs(**payload()))
        a = await pine_ops.compile_pine(state.source.id, BackgroundTasks(), state, body=data)
        b = await pine_ops.compile_pine(state.source.id, BackgroundTasks(), state, body=data)
        assert a["operation_id"] != b["operation_id"]
        state.admission_identity = None
        with pytest.raises(Exception) as e:
            await pine_ops.compile_pine(state.source.id, BackgroundTasks(), state, body=data)
        assert getattr(e.value, "status_code", None) == 503

    asyncio.run(scenario())


def test_openapi_describes_optional_strict_inline_libraries(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/pine/{source_id}/compile"]["post"]
    assert operation["requestBody"].get("required", False) is False
    model = schema["components"]["schemas"]["LibraryInputs"]
    assert model["additionalProperties"] is False
    assert set(model["required"]) == {"lock", "sources", "expected_lock_hash"}


@pytest.mark.parametrize("command", ["compile", "pine-compile", "validate"])
def test_cli_admits_lock_in_standard_commands_and_persists_only_compile(
    tmp_path, monkeypatch, command
):
    from openpine.cli.main import pine
    from openpine.config import OpenPineConfig
    from openpine import admission
    from openpine.pine import registry as registry_module

    data = payload()
    path = write_project(tmp_path / "libs", data)
    config = OpenPineConfig(workspace_root=tmp_path, data_dir=tmp_path / "data")
    db = tmp_path / "pine.sqlite"
    registry = SQLitePineSourceRegistry(db)
    source = registry.add_source(root().source_text, "consumer")
    registry.close()
    monkeypatch.setattr(
        registry_module, "SQLitePineSourceRegistry", lambda: SQLitePineSourceRegistry(db)
    )
    monkeypatch.setattr("openpine.artifacts.store.OpenPineConfig.load", lambda: config)
    monkeypatch.setattr("openpine.build_identity.compiler_producer_commits", lambda: dict(COMMITS))
    monkeypatch.setattr(
        admission,
        "admit_configured_deployment",
        lambda **kwargs: admission.admit_deployment(
            mode=kwargs["mode"], deployment=make_deployment_identity()
        ),
    )
    result = CliRunner().invoke(
        pine,
        [
            command,
            "consumer",
            "--library-lock",
            str(path),
            "--expected-library-lock-hash",
            data["expected_lock_hash"],
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    registry = SQLitePineSourceRegistry(db)
    try:
        active = registry.get_source("consumer").active_artifact_id
        assert bool(active) == (command != "validate")
        if active:
            assert ArtifactStore(config.data_dir / "artifacts").get_artifact(active, source.id)[
                "generated_artifact"
            ]
    finally:
        registry.close()


@pytest.mark.parametrize("command", ["compile", "validate"])
def test_cli_half_lock_arguments_fail_with_nonzero_exit(tmp_path, monkeypatch, command):
    from openpine.cli.main import pine
    from openpine import admission

    monkeypatch.setattr(
        admission,
        "admit_configured_deployment",
        lambda **kwargs: admission.admit_deployment(
            mode=kwargs["mode"], deployment=make_deployment_identity()
        ),
    )
    result = CliRunner().invoke(
        pine, [command, "consumer", "--library-lock", str(tmp_path / "missing")]
    )
    assert result.exit_code != 0 and "required together" in result.output


@pytest.mark.parametrize("route", ["compile", "validate"])
def test_missing_compiler_identity_is_structured_and_never_admitted(
    client, state, monkeypatch, route
):
    from openpine.build_identity import BuildIdentityError

    def unavailable():
        raise BuildIdentityError("exact compiler producer commit is unavailable: pine2ast")

    monkeypatch.setattr("openpine.build_identity.compiler_producer_commits", unavailable)
    response = client.post(f"/api/pine/{state.source.id}/{route}", json={"libraries": payload()})
    if route == "compile":
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "OPENPINE_BUILD_IDENTITY"
    else:
        assert response.status_code == 200 and not response.json()["valid"]
        assert response.json()["diagnostics"][0]["code"] == "OPENPINE_BUILD_IDENTITY"
    assert state.artifact_store.list_artifacts(state.source.id) == []
