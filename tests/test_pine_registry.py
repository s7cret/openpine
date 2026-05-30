from __future__ import annotations

import pytest

from openpine.pine.registry import SQLitePineSourceRegistry


def test_pine_source_registry_persists_and_loads_by_id_or_name(tmp_path) -> None:
    db_path = tmp_path / "openpine.sqlite"
    registry = SQLitePineSourceRegistry(db_path=db_path)
    source = registry.add_source('//@version=6\nstrategy("x")', "demo")
    registry.set_active_artifact(source.id, "artifact-1")
    registry.close()

    reloaded = SQLitePineSourceRegistry(db_path=db_path)
    try:
        by_id = reloaded.get_source(source.id)
        by_name = reloaded.get_source("demo")
    finally:
        reloaded.close()

    assert by_id.id == source.id
    assert by_name.id == source.id
    assert by_id.active_artifact_id == "artifact-1"
    assert by_id.source_type == "strategy"


def test_pine_source_registry_remove_source_by_name(tmp_path) -> None:
    registry = SQLitePineSourceRegistry(db_path=tmp_path / "openpine.sqlite")
    try:
        source = registry.add_source('//@version=6\nindicator("x")', "demo")

        registry.remove_source("demo")

        assert registry.list_sources() == []
        with pytest.raises(KeyError):
            registry.get_source(source.id)
    finally:
        registry.close()
