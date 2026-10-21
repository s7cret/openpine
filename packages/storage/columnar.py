"""Bounded DuckDB connections; no shared writable DuckDB database file."""

from contextlib import contextmanager


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
    finally:
        db.close()


def quoted(path):
    return "'" + str(path).replace("'", "''") + "'"
