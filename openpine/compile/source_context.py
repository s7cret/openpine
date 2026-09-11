"""One source preparation and provenance path for compile, validate and storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pine2ast.libraries import (
    LibraryError,
    LibraryStore,
    LinkedSource,
    has_library_imports,
    link_libraries,
)
from pine2ast.libraries.qualifier_context import LibraryQualifierContext
from pine2ast.libraries.store import canonical, source_hash

from .library_inputs import admit_library_payload


@dataclass(frozen=True)
class PreparedSource:
    original: str
    source_name: str
    linked: LinkedSource | None = None
    lock_hash: str | None = None

    @property
    def code(self) -> str:
        return self.linked.code if self.linked is not None else self.original

    def metadata(self) -> dict[str, Any]:
        value = {
            "source_name": self.source_name,
            "original_source_hash": source_hash(self.original),
        }
        if self.lock_hash is not None:
            value["library_lock_hash"] = self.lock_hash
        if self.linked is not None:
            value["library_linkage"] = self.linked.receipt()
        return value


def prepare_source(
    source: str, source_name: str, *, library_store=None, library_payload=None
) -> PreparedSource:
    if library_store is not None and library_payload is not None:
        raise LibraryError(
            "P2A_LIBRARY_INPUT", "provide one library input, not both store and payload"
        )
    if library_payload is not None:
        library_store = admit_library_payload(library_payload)
    if library_store is not None:
        if not isinstance(library_store, LibraryStore):
            raise LibraryError(
                "P2A_LIBRARY_INPUT", "library_store must be an admitted LibraryStore"
            )
        # Reject hand-constructed/rebound stores even when the source has no imports.
        library_store = LibraryStore.admit(
            library_store.lock(),
            {
                row["ref"]: library_store.source(row["ref"])
                for row in library_store.lock()["libraries"]
            },
            expected_hash=library_store.content_hash,
        )
    if has_library_imports(source, source_name=source_name):
        if library_store is None:
            raise LibraryError(
                "P2A_LIBRARY_REQUIRED",
                "script imports libraries: supply a pinned library lock and sources",
                source=source_name,
            )
        linked = link_libraries(source, library_store, source_name=source_name)
    else:
        linked = None
    return PreparedSource(
        source, source_name, linked, library_store.content_hash if library_store else None
    )


def stored_source_context(source_text: str, consumer_bundle: dict) -> PreparedSource:
    """Reconstruct from the sealed bundle, never from mutable compile metadata.

    Admit performs source-bound replay. The separately stored root MUST match
    the original root in that context, not the generated virtual compilation text.
    """
    payload = consumer_bundle.get("library_context")
    if payload is None:
        return PreparedSource(source_text, "<memory>")
    context = LibraryQualifierContext.admit(payload)
    receipt = context.to_dict()["linkage_receipt"]
    name = receipt["root_source_name"]
    if receipt["sources"][name]["raw_text"] != source_text:
        raise ValueError("stored root source differs from sealed library context")
    return PreparedSource(source_text, name, LinkedSource(context.code, canonical(receipt)))


def projected_source_map(source_map: dict, prepared: PreparedSource) -> dict | None:
    """Sidecar projection; never mutate the sealed Python/virtual-Pine map."""
    linked = prepared.linked
    if linked is None:
        return None
    entries = source_map["entries"]
    offsets = [
        e["source_span"]["start_offset"] for e in entries if e.get("source_span") is not None
    ]
    locations = iter(linked.original_locations(offsets))
    rows = []
    for entry in entries:
        span = entry.get("source_span")
        rows.append(
            {
                "python_start": entry["python_start"],
                "python_end": entry["python_end"],
                "source_node_id": entry["source_node_id"],
                "location": next(locations) if span is not None else None,
                "virtual_span": span,
            }
        )
    receipt = linked.receipt()
    body = {
        "schema_id": "openpine.original_source_projection.v1",
        "coordinate_basis": "normalized_pine_unicode",
        "source_map_hash": source_map["content_hash"],
        "linkage_hash": receipt["content_hash"],
        "entries": rows,
    }
    return {**body, "content_hash": source_hash(canonical(body))}
