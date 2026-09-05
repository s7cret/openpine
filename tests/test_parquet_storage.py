"""Real codec and filesystem regressions for the single production storage path."""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from openpine.storage import parquet


def test_real_parquet_roundtrip_and_projection(tmp_path):
    path = tmp_path / "symbol=BTCUSDT" / "bars.parquet"
    expected = pd.DataFrame({"symbol": ["BTCUSDT", "ETHUSDT"], "close": [1.25, None]})
    dtype = parquet.schema([("symbol", "string"), ("close", "float64", True)])
    parquet.write_dataframe(expected, path, schema=dtype)
    assert path.read_bytes()[:4] == path.read_bytes()[-4:] == b"PAR1"
    pd.testing.assert_frame_equal(parquet.read_dataframe(path), expected)
    assert parquet.read_dataframe(path, columns=["symbol"]).columns.tolist() == ["symbol"]
    assert parquet.row_count(path) == 2
    with pq.ParquetFile(path) as file:
        assert file.schema_arrow.field("symbol").nullable is False
        assert file.schema_arrow.field("close").nullable is True


@pytest.mark.parametrize("legacy_flag", [None, "0", "1"])
@pytest.mark.parametrize("kind", ["pickle", "json", "corrupt"])
def test_legacy_formats_never_trigger_deserialization(tmp_path, monkeypatch, legacy_flag, kind):
    if legacy_flag is None:
        monkeypatch.delenv("OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET", raising=False)
    else:
        monkeypatch.setenv("OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET", legacy_flag)
    path = tmp_path / "legacy.parquet"
    frame = pd.DataFrame({"n": [1]})
    if kind == "pickle":
        frame.to_pickle(path)
    elif kind == "json":
        frame.to_json(path, orient="table")
    else:
        path.write_bytes(b"not parquet")
    def forbidden(*_a, **_k):
        raise AssertionError("legacy decoder must never execute")
    monkeypatch.setattr(pd, "read_pickle", forbidden)
    monkeypatch.setattr(pd, "read_json", forbidden)
    with pytest.raises(pa.ArrowInvalid):
        parquet.read_dataframe(path)
    with pytest.raises(pa.ArrowInvalid):
        parquet.row_count(path)


@pytest.mark.parametrize("failure", ["write", "replace", "schema"])
def test_failed_write_preserves_existing_file_and_cleans_temp(tmp_path, monkeypatch, failure):
    path = tmp_path / "bars.parquet"
    parquet.write_dataframe(pd.DataFrame({"n": [1]}), path)
    original = path.read_bytes()
    def fail(*_a, **_k):
        raise OSError("injected write failure")
    kwargs = {}
    if failure == "write":
        monkeypatch.setattr(parquet.pq, "write_table", fail)
    elif failure == "replace":
        monkeypatch.setattr(parquet.os, "replace", fail)
    else:
        kwargs["schema"] = parquet.schema([("missing", "int64")])
    with pytest.raises((OSError, KeyError)):
        parquet.write_dataframe(pd.DataFrame({"n": [2]}), path, **kwargs)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bars.parquet"]


def test_row_count_uses_only_footer(tmp_path, monkeypatch):
    path = tmp_path / "bars.parquet"
    parquet.write_dataframe(pd.DataFrame({"n": list(range(20))}), path)
    def forbidden(*_a, **_k):
        raise AssertionError("row count loaded data")
    monkeypatch.setattr(pq.ParquetFile, "read", forbidden)
    monkeypatch.setattr(parquet, "read_dataframe", forbidden)
    assert parquet.row_count(path) == 20


@pytest.mark.parametrize("fields", [[("x", "bad")], [("x", "int64", "yes")],
                                     [("", "int64")], [("x",)],
                                     [("x", "int64"), ("x", "string")]])
def test_schema_rejects_invalid_declarations(fields):
    with pytest.raises(ValueError):
        parquet.schema(fields)
