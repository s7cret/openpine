"""Default collection uses a declared additive lock, preserving the old baseline."""

import pytest

from openpine.verification.execution_cli import inventory_lock_path


def test_current_inventory_is_selected_without_overwriting_historical_file(tmp_path):
    folder = tmp_path / "verification"
    folder.mkdir()
    historical = folder / "inventory.json"
    current = folder / "current.json"
    historical.write_text("historical")
    current.write_text("additive")
    assert inventory_lock_path(tmp_path, {"inventory_lock": "verification/current.json"}) == current
    assert historical.read_text() == "historical"
    assert inventory_lock_path(tmp_path, {}) == historical


def test_explicit_operator_inventory_override_preserves_existing_interface(tmp_path):
    override = tmp_path / "operator.json"
    assert inventory_lock_path(tmp_path / "host", {}, override) == override


@pytest.mark.parametrize("name", ["../escape.json", "/tmp/outside.json", "verification//current.json", "verification/./current.json", None, 1])
def test_declared_default_inventory_cannot_escape_source_scope(tmp_path, name):
    with pytest.raises(ValueError):
        inventory_lock_path(tmp_path, {"inventory_lock": name})


def test_declared_inventory_refuses_a_symlink_to_external_lock(tmp_path):
    root = tmp_path / "host"
    root.mkdir()
    outside = tmp_path / "external.json"
    outside.write_text("foreign")
    (root / "inventory.json").symlink_to(outside)
    with pytest.raises(ValueError):
        inventory_lock_path(root, {"inventory_lock": "inventory.json"})
