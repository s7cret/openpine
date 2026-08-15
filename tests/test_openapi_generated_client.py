import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _gen():
    spec = importlib.util.spec_from_file_location(
        "generate_openapi_ts", ROOT / "scripts" / "generate_openapi_ts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_openapi_includes_jobs_compare() -> None:
    text = (ROOT / "openpine-ui/src/api/generated/openapi.ts").read_text(
        encoding="utf-8"
    )
    assert "/api/jobs/compare" in text
    assert "Do not edit by hand" in text


def test_generator_counts_unique_operations() -> None:
    gen = _gen()
    schema = {
        "paths": {
            "/api/jobs/compare": {"get": {}, "parameters": []},
            "/api/jobs": {"get": {}, "post": {}},
        }
    }
    rows = gen.operations(schema)
    assert ("GET", "/api/jobs/compare") in rows
    assert ("POST", "/api/jobs") in rows
    assert "OPENAPI_OPERATION_COUNT = 3" in gen.render(rows)
