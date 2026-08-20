from pathlib import Path
import importlib.util
import json
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "stack-candidate-5.0.0-rc.2.json"
PIN = "51e32ebaaf02eecb81443e8ca7e89b2543cb25a3"
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


def test_candidate_manifest_pins_eight_repos_and_is_not_a_release() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["schema"] == "openpine.stack-candidate.v1"
    assert payload["id"] == "5.0.0-rc.2"
    assert payload["not_a_release"] is True
    assert payload["contracts_pin"] == PIN
    components = payload["components"]
    assert set(components) == REQUIRED
    assert components["openpine-contracts"]["sha"] == PIN
    for name, row in components.items():
        if name == "openpine" and row["sha"] == "THIS_CHECKOUT":
            continue
        assert SHA40.fullmatch(row["sha"]), name
    lock = json.loads(
        (ROOT / "openpine" / "stack-lock.json").read_text(encoding="utf-8")
    )
    assert lock != payload


def test_stack_ci_separates_feature_candidate_from_production_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "stack-ci.yml").read_text(
        encoding="utf-8"
    )

    assert "resolve_stack_candidate.py" in workflow
    assert "build_candidate_wheelhouse.py" in workflow
    assert "steps.stack.outputs.mode == 'production'" in workflow
    assert "steps.stack.outputs.mode == 'candidate'" in workflow
    assert "python -m pytest -q tests/test_stack_candidate.py" in workflow
    assert "name: Production sibling release reports" in workflow
    for component in REQUIRED:
        assert f"--checkout {component}=" in workflow


def test_backend_ci_uses_the_same_candidate_resolver_and_checkouts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "resolve_stack_candidate.py" in workflow
    assert "build_candidate_wheelhouse.py" in workflow
    for component in REQUIRED:
        assert f"--checkout {component}=" in workflow


def test_backend_release_gate_separates_candidate_from_production_lock() -> None:
    release_gate = (ROOT / "scripts" / "release_gate.sh").read_text(encoding="utf-8")

    assert "resolve_stack_candidate.py --root ." in release_gate
    assert "stack-candidate-5.0.0-rc.2.json" not in release_gate
    assert "$PYTHON -m pytest -q tests/test_stack_candidate.py" in release_gate
    assert "$PYTHON -m openpine.release --root ." in release_gate
    assert "export PYTHON=${PYTHON:-python}" in release_gate


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


def test_candidate_resolver_emits_manifest_identity() -> None:
    resolver = _resolver()
    outputs = dict(resolver.github_outputs(MANIFEST))

    assert outputs["mode"] == "candidate"
    assert outputs["candidate_path"] == MANIFEST.name
    assert outputs["pine2ast_repo"] == "s7cret/pine2ast"
    assert outputs["pine2ast_sha"] == "ce1f504ef0e47764043024800ba406b7dae8a43f"
    assert outputs["openpine_sha"] == "THIS_CHECKOUT"


def test_candidate_resolver_rejects_github_output_injection(tmp_path: Path) -> None:
    resolver = _resolver()
    manifest = tmp_path / "stack-candidate-evil.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "openpine.stack-candidate.v1",
                "not_a_release": True,
                "components": {
                    "pinelib": {
                        "repo": "s7cret/pinelib\nmode=production",
                        "sha": "0" * 40,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(resolver.CandidateSelectionError, match="invalid repository"):
        resolver.load_candidate(manifest)


def test_openapi_drift_job_compares_generated_client() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "generate_openapi_ts.py .contract/openapi.json .contract/openapi.ts" in workflow
    assert "src/api/generated/openapi.ts" in workflow
