"""Real build/download inputs; a failed environment gate stays explicitly failed."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

def pytest_addoption(parser):
    parser.addoption("--real-prepare-work", type=Path, required=True)


@pytest.fixture(scope="session")
def candidate_inputs(pytestconfig):
    work = pytestconfig.getoption("--real-prepare-work").resolve(strict=True)
    bundle = work / "bundle"
    report = json.loads((work / "candidate-wheel-inputs.json").read_bytes())
    assert report["full_prepare_ok"] is False
    assert report["full_prepare_blocker"] == "optimizer-process per-thread procfs children absent in local kernel"
    assert report["environment"]["python"] == "3.13.5"
    assert report["environment"]["gil_enabled"] is True
    def verify_inputs():
        assert {path.name for path in (bundle / "wheelhouse").iterdir()} == set(report["files"])
        for name, row in report["files"].items():
            path = bundle / "wheelhouse" / name
            assert path.stat().st_size == row["size"] and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    verify_inputs()
    helper = work / "stack/openpine/scripts/rc6_stabilization/clean_build.py"
    spec = importlib.util.spec_from_file_location("real_preflight_clean_build", helper)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    trees = json.loads((bundle / "package-trees.json").read_bytes())
    installed = json.loads((bundle / "installed-package-trees.json").read_bytes())
    origins = json.loads((bundle / "installed-origins.json").read_bytes())
    assert len(trees) == len(installed["components"]) == len(origins) == 8 and installed["isolated"] is True
    for component, row in trees.items():
        wheel = next((bundle / "wheelhouse").glob(component.replace("-", "_") + "-*.whl"))
        owner.verify_wheel_tree(work / "build-sources" / component, wheel, expected=row["package_tree"])
    assert all(Path(path).is_relative_to(work / "venv") for path in origins.values())
    yield bundle, report
    verify_inputs()
