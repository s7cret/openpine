"""Compatibility imports for specification maintenance tools."""

import sys

from packages.compiler.semantic.specification import (
    ROOT, UNORDERED, Snapshot, SpecError, canonical, coverage, encode, file_map,
    index, load_snapshot, loads, read_json, require_python, resolution_problem,
    safe_path, semantic_projection, sha, validate_document, validate_identities,
    validate_registry, validate_schema, value_sha, verify_files,
)
