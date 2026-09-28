"""Runtime caches cannot mutate the frozen eight-source CI identity."""

from openpine.verification.execution_identity import source_snapshot


def test_marketdata_root_cache_is_not_an_execution_source(tmp_path):
    root = tmp_path / "marketdata-provider"
    source = root / "marketdata_provider" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("VERSION = 1\n")
    baseline = source_snapshot({"marketdata-provider": root})

    cache = root / ".marketdata-cache"
    cache.mkdir()
    (cache / "current.sqlite").write_bytes(b"runtime cache")
    (cache / "index.sqlite").write_bytes(b"runtime index")
    assert source_snapshot({"marketdata-provider": root}) == baseline

    source.write_text("VERSION = 2\n")
    assert source_snapshot({"marketdata-provider": root}) != baseline
