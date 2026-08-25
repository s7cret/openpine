from pathlib import Path
import importlib.util
import json
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "candidates" / "stack-candidate-5.0.0-rc.5.template.json"
HISTORICAL = (
    ROOT / "candidates" / "historical" / "stack-candidate-5.0.0-rc.2.json"
)
PIN = "6b5e67445e2772057cd877e158c7aa0c58bdfe37"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = {
    "openpine-contracts",
    "marketdata-provider",
    "pinelib",
    "backtest_engine",
    "pine2ast",
    "ast2python",
    "optimizer",
    "openpine",
}


def _resolver():
    spec = importlib.util.spec_from_file_location(
        "resolve_stack_candidate", ROOT / "scripts" / "resolve_stack_candidate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materializer():
    spec = importlib.util.spec_from_file_location(
        "materialize_stack_candidate",
        ROOT / "scripts" / "materialize_stack_candidate.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_template_pins_eight_repos_and_is_not_active() -> None:
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert payload["schema"] == "openpine.stack-candidate-template.v1"
    assert payload["id"] == "5.0.0-rc.5"
    assert payload["not_a_release"] is True
    components = payload["components"]
    assert set(components) == REQUIRED
    assert components["openpine-contracts"]["sha"] == PIN
    for name, row in components.items():
        assert row["version"] == "5.0.0rc5"
        if name == "openpine":
            assert "sha" not in row
            continue
        assert SHA40.fullmatch(row["sha"]), name
    assert _resolver().resolve_candidate(ROOT) is None
    historical = json.loads(HISTORICAL.read_text(encoding="utf-8"))
    assert historical["components"]["openpine"]["sha"] == "THIS_CHECKOUT"
    lock = json.loads(
        (ROOT / "openpine" / "stack-lock.json").read_text(encoding="utf-8")
    )
    assert lock != payload


def test_stack_ci_separates_feature_candidate_from_production_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stack-ci.yml").read_text(
        encoding="utf-8"
    )

    assert "resolve_stack_candidate.py" in workflow
    assert "materialize_stack_candidate.py" in workflow
    assert "finalize_stack_candidate.py" in workflow
    assert "install_candidate_wheelhouse.py" in workflow
    assert "--openpine-sha \"$GITHUB_SHA\"" in workflow
    assert '--root "$RUNNER_TEMP/openpine-candidate"' in workflow
    assert "build_candidate_wheelhouse.py" in workflow
    assert " -e " not in workflow
    assert "steps.stack.outputs.mode == 'production'" in workflow
    assert "steps.stack.outputs.mode == 'candidate'" in workflow
    assert "openpine-candidate-venv/bin/python" in workflow
    assert "tests/test_stack_candidate.py" in workflow
    assert "name: Production sibling release reports" in workflow
    for component in REQUIRED:
        assert f"--checkout {component}=" in workflow


def test_backend_ci_uses_the_same_candidate_resolver_and_checkouts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "resolve_stack_candidate.py" in workflow
    assert "materialize_stack_candidate.py" in workflow
    assert "finalize_stack_candidate.py" in workflow
    assert "install_candidate_wheelhouse.py" in workflow
    assert "--openpine-sha \"$GITHUB_SHA\"" in workflow
    assert '--root "$RUNNER_TEMP/openpine-candidate"' in workflow
    assert "build_candidate_wheelhouse.py" in workflow
    assert 'rm -f "$RUNNER_TEMP/openpine-candidate/${{ steps.stack.outputs.candidate_path }}"' in workflow
    assert " -e " not in workflow
    for component in REQUIRED:
        assert f"--checkout {component}=" in workflow


@pytest.mark.parametrize("workflow_name", ["ci.yml", "stack-ci.yml"])
def test_feature_branch_pushes_do_not_duplicate_pull_request_runs(
    workflow_name: str,
) -> None:
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert workflow["on"]["pull_request"] == {"branches": ["main"]}
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "true",
    }


def test_backend_release_gate_separates_candidate_from_production_lock() -> None:
    release_gate = (ROOT / "scripts" / "release_gate.sh").read_text(encoding="utf-8")

    assert "OPENPINE_CANDIDATE_MANIFEST" in release_gate
    assert '--root "$candidate_root" --require-stage wheel-bound' in release_gate.replace(
        "\\\n", ""
    )
    assert "materialized candidate manifest required" in release_gate
    assert "stack-candidate-5.0.0-rc.2.json" not in release_gate
    assert "$PYTHON -m pytest -q tests/test_stack_candidate.py" in release_gate
    assert "$PYTHON -m openpine.release --root ." in release_gate
    assert "export PYTHON=${PYTHON:-python}" in release_gate
    assert "export PYTHONPATH" not in release_gate


def test_backend_ci_installs_required_sandbox_runtime() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "Install Bubblewrap sandbox runtime" in workflow
    assert (
        "apt-get install --no-install-recommends -y "
        "apparmor-profiles apparmor-utils bubblewrap"
    ) in workflow
    assert "/usr/share/apparmor/extra-profiles/bwrap-userns-restrict" in workflow
    assert "apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict" in workflow
    assert "--unshare-net -- /usr/bin/true" in workflow
    assert "useradd --system --no-create-home" in workflow
    assert "--shell /usr/sbin/nologin openpine-worker" in workflow
    assert "sudo -n -u openpine-worker -- /usr/bin/id -u" in workflow


def test_backend_ci_fetches_history_for_frozen_ref_verification() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["backend"]["steps"]
    sibling_checkouts = [
        step
        for step in steps
        if step.get("uses") == "actions/checkout@v4"
        and str(step.get("with", {}).get("path", "")).startswith("stack/")
    ]

    assert len(sibling_checkouts) == 7
    assert all(step["with"].get("fetch-depth") == 0 for step in sibling_checkouts)


def test_openapi_drift_builds_ui_before_node_packaging_tests() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    commands = [
        step["run"]
        for step in workflow["jobs"]["openapi-drift"]["steps"]
        if "run" in step
    ]

    assert commands.index("npm ci") < commands.index("npm run build")
    assert commands.index("npm run build") < commands.index("npm run test:node")


def test_candidate_resolver_requires_zero_or_one_manifest(tmp_path: Path) -> None:
    resolver = _resolver()
    assert resolver.resolve_candidate(tmp_path) is None

    first = tmp_path / "stack-candidate-a.json"
    first.write_text("{}", encoding="utf-8")
    assert resolver.resolve_candidate(tmp_path) == first

    (tmp_path / "stack-candidate-b.json").write_text("{}", encoding="utf-8")
    with pytest.raises(resolver.CandidateSelectionError, match="exactly one"):
        resolver.resolve_candidate(tmp_path)


def test_candidate_resolver_rejects_unsafe_filename(tmp_path: Path) -> None:
    resolver = _resolver()
    candidate = tmp_path / "stack-candidate-bad\nmode=production.json"
    candidate.write_text("{}", encoding="utf-8")

    with pytest.raises(resolver.CandidateSelectionError, match="invalid candidate"):
        resolver.resolve_candidate(tmp_path)


def test_candidate_resolver_emits_manifest_identity(tmp_path: Path) -> None:
    resolver = _resolver()
    materializer = _materializer()
    payload = materializer.materialize_candidate(
        json.loads(TEMPLATE.read_text(encoding="utf-8")),
        openpine_sha="d" * 40,
        created_at_utc="2026-08-20T21:00:00Z",
        provenance={"builder": "test", "run_id": "1"},
    )
    manifest = tmp_path / "stack-candidate-5.0.0-rc.5.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    outputs = dict(resolver.github_outputs(manifest))

    assert outputs["mode"] == "candidate"
    assert outputs["candidate_path"] == manifest.name
    assert outputs["pine2ast_repo"] == "s7cret/pine2ast"
    assert outputs["pine2ast_sha"] == "325ddd17f4ced3c42739fa58bc902a927c2d4ac6"
    assert outputs["openpine_sha"] == "d" * 40


def test_candidate_resolver_rejects_github_output_injection(tmp_path: Path) -> None:
    resolver = _resolver()
    materializer = _materializer()
    manifest = tmp_path / "stack-candidate-evil.json"
    payload = materializer.materialize_candidate(
        json.loads(TEMPLATE.read_text(encoding="utf-8")),
        openpine_sha="d" * 40,
        created_at_utc="2026-08-20T21:00:00Z",
        provenance={"builder": "test", "run_id": "1"},
    )
    payload["components"]["pinelib"]["repo"] = "s7cret/pinelib\nmode=production"
    payload["manifest_hash"] = resolver.candidate_manifest_hash(payload)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(resolver.CandidateSelectionError, match="invalid repository"):
        resolver.load_candidate(manifest)


def test_openapi_drift_job_compares_generated_client() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "generate_openapi_ts.py .contract/openapi.json .contract/openapi.ts" in workflow
    assert "src/api/generated/openapi.ts" in workflow
