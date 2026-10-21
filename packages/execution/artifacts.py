"""Portable Typed IR. Compatibility and runtime admission are checked by Session."""

from packages.contracts import Diagnostic, OpenPineError, ProgramIR
from packages.contracts.codec import encode, decode
from packages.contracts.wire import loads, read_json
from packages.runtime.engine import program_hash

ARTIFACT_SCHEMA = "openpine.program-artifact.1"


def export_program(program):
    return {"schema_version": ARTIFACT_SCHEMA, "program_sha256": program_hash(program), "ir": encode(program)}


def import_program(payload):
    try:
        if type(payload) is not dict or payload.keys() != {"schema_version", "program_sha256", "ir"} or payload["schema_version"] != ARTIFACT_SCHEMA:
            raise ValueError("Unknown artifact schema/fields.")
        program = decode(payload["ir"], ProgramIR)
        if program_hash(program) != payload["program_sha256"]:
            raise ValueError("Program content hash mismatch.")
        return program
    except (ValueError, TypeError, RecursionError, KeyError) as error:
        raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE", "ARTIFACT_INVALID", str(error))) from error


def read_program(path):
    try:
        payload = read_json(path)
        # CLI compile responses contain the identical standalone artifact.
        if type(payload) is dict and payload.get("status") == "compiled" and "artifact" in payload:
            payload = payload["artifact"]
        return import_program(payload)
    except ValueError as error:
        if isinstance(error, OpenPineError):
            raise
        raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE", "ARTIFACT_INVALID", str(error))) from error
