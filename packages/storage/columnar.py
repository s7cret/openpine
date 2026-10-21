"""Bounded DuckDB connections; no shared writable DuckDB database file."""

from contextlib import contextmanager
from packages.contracts import Diagnostic,OpenPineError


@contextmanager
def connection(*, threads=1, memory_mb=128, temporary=None):
    import duckdb
    if duckdb.__version__ != "2.0.0.dev2610192346":
        raise RuntimeError("The storage profile requires the pinned DuckDB 2.0 build.")
    db = duckdb.connect(":memory:", config={"threads": str(threads),
        "memory_limit": f"{memory_mb}MB", "enable_external_access": "true"})
    try:
        db.execute("SET autoinstall_known_extensions=false; SET autoload_known_extensions=false")
        if temporary:
            db.execute("SET temp_directory=?", [str(temporary)])
        db.execute("SET max_temp_directory_size='512MB'")
        yield db
    except duckdb.OutOfMemoryException as error:
        raise OpenPineError(Diagnostic('RESOURCE_EXHAUSTED','COLUMNAR_MEMORY',
            'Columnar I/O exceeded its pinned per-connection memory budget.')) from error
    finally:
        db.close()


def quoted(path):
    return "'" + str(path).replace("'", "''") + "'"
