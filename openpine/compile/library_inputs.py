"""Explicit local/HTTP library inputs; admission remains owned by Pine2AST.

HTTP uses inline source text, never a path on the server. The expected lock hash
is supplied separately from the lock, and is not inferred from received bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pine2ast.libraries import LibraryError, LibraryStore
from pine2ast.libraries.store import HASH, MAX_LIBRARIES, MAX_SOURCE_BYTES, MAX_TOTAL_BYTES


def admit_library_payload(payload: Mapping[str, Any] | None) -> LibraryStore | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping) or set(payload) != {
        "lock",
        "sources",
        "expected_lock_hash",
    }:
        raise LibraryError(
            "P2A_LIBRARY_INPUT", "expected lock, sources and expected_lock_hash only"
        )
    sources = payload["sources"]
    if not isinstance(sources, Mapping) or not 1 <= len(sources) <= MAX_LIBRARIES:
        raise LibraryError("P2A_LIBRARY_LIMIT", "sources must contain 1..64 exact publications")
    # Bound text before copying or canonicalizing it. LibraryStore owns all schema,
    # path, revision and content-hash checks; this is not an alternative resolver.
    total = 0
    for value in sources.values():
        if type(value) is not str:
            raise LibraryError("P2A_LIBRARY_SOURCE", "source must be UTF-8 text")
        if len(value) > MAX_SOURCE_BYTES:
            raise LibraryError("P2A_LIBRARY_LIMIT", "library source size limit exceeded")
        size = len(value.encode("utf-8"))
        total += size
        if size > MAX_SOURCE_BYTES or total > MAX_TOTAL_BYTES:
            raise LibraryError("P2A_LIBRARY_LIMIT", "library source size limit exceeded")
    return LibraryStore.admit(payload["lock"], sources, expected_hash=payload["expected_lock_hash"])


def load_library_lock(
    lock_path: str | Path | None, expected_hash: str | None
) -> LibraryStore | None:
    """CLI-only file entry point. Capture no-follow inputs once before compilation."""
    if lock_path is None and expected_hash is None:
        return None
    if lock_path is None or expected_hash is None:
        raise LibraryError(
            "P2A_LIBRARY_INPUT",
            "--library-lock and --expected-library-lock-hash are required together",
        )
    return LibraryStore.from_directory(lock_path, expected_hash=expected_hash)


def requested_lock_hash(*, library_store=None, library_payload=None) -> str | None:
    """Audit-only identity, including failed linking. It grants no admission."""
    value = (
        library_payload.get("expected_lock_hash")
        if isinstance(library_payload, Mapping)
        else library_store.content_hash
        if isinstance(library_store, LibraryStore)
        else None
    )
    return value if type(value) is str and HASH.fullmatch(value) else None
