"""Fixture plans freeze the launch scope, preserving every real dependency."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
from pathlib import Path

from openpine.verification.execution_identity import environment_snapshot, hash_file

HOST = Path(__file__).resolve().parents[1]


def probe_roots(tmp_path):
    root = tmp_path / "source"
    package = root / "openpine" / "verification"
    package.mkdir(parents=True)
    (root / "openpine" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    for name in ("execution_identity.py", "identity.py"):
        shutil.copy2(HOST / "openpine" / "verification" / name, package / name)
    helper = tmp_path / "helper.py"
    helper.write_text("# preserved fixture input\n")
    return {"openpine": root}, {
        "helper": {"path": str(helper), "sha256": hash_file(helper)}
    }


def metadata(root, name, version):
    path = root / (name.replace("-", "_") + ".egg-info")
    path.mkdir(parents=True)
    (path / "PKG-INFO").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )


def test_parent_only_distribution_metadata_is_not_a_child_dependency(
    tmp_path, monkeypatch
):
    from rc6_tests.stabilization_fixture import freeze_fixture_environment

    roots, inputs = probe_roots(tmp_path)
    poison = tmp_path / "parent-only"
    metadata(poison, "fixture-parent-only", "1.2.3")
    monkeypatch.syspath_prepend(str(poison))
    monkeypatch.setenv("PYTHONPATH", str(poison))
    assert environment_snapshot()["distributions"]["fixture-parent-only"] == "1.2.3"
    frozen = freeze_fixture_environment(roots, tmp_path / "probe", inputs)
    assert "fixture-parent-only" not in frozen["distributions"]
    assert frozen["distributions"]["pytest"] == importlib.metadata.version("pytest")
    assert frozen["executable_sha256"] == environment_snapshot()["executable_sha256"]


def test_launch_visible_distribution_metadata_remains_in_full_fingerprint(tmp_path):
    from rc6_tests.stabilization_fixture import freeze_fixture_environment

    roots, inputs = probe_roots(tmp_path)
    metadata(roots["openpine"], "fixture-child-required", "4.5.6")
    frozen = freeze_fixture_environment(roots, tmp_path / "probe", inputs)
    assert frozen["distributions"]["fixture-child-required"] == "4.5.6"
    actual = json.loads((tmp_path / "probe" / "command" / "stdout.log").read_text())
    assert frozen == actual
    metadata_file = roots["openpine"] / "fixture_child_required.egg-info" / "PKG-INFO"
    metadata_file.write_text(metadata_file.read_text().replace("4.5.6", "4.5.7"))
    changed = freeze_fixture_environment(roots, tmp_path / "probe-changed", inputs)
    assert changed["distributions"]["fixture-child-required"] == "4.5.7"
    assert frozen["content_hash"] != changed["content_hash"]
