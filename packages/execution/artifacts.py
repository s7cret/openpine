"""Portable Typed IR. Compatibility and runtime admission are checked by Session."""

from packages.contracts import Diagnostic, OpenPineError, ProgramIR
from packages.contracts.codec import encode, decode
from packages.contracts.wire import loads, read_json
from packages.runtime.engine import program_hash
from packages.execution.identity import build_identity, EXECUTION_SCHEME

ARTIFACT_SCHEMA = "openpine.program-artifact.2"


def export_program(program):
    return {"schema_version": ARTIFACT_SCHEMA, "program_sha256": program_hash(program), "ir": encode(program),
        "compatibility_scheme":EXECUTION_SCHEME, "build":build_identity()}


def import_program(payload):
    try:
        if type(payload) is not dict:
            raise ValueError("Invalid artifact.")
        legacy = payload.get("schema_version") == "openpine.program-artifact.1"
        keys = {"schema_version","program_sha256","ir"} | (set() if legacy else {"compatibility_scheme","build"})
        if payload.keys() != keys or payload["schema_version"] not in ("openpine.program-artifact.1", ARTIFACT_SCHEMA):
            raise ValueError("Unknown artifact schema/fields.")
        if not legacy:
            build = payload["build"]
            if (payload["compatibility_scheme"] != EXECUTION_SCHEME or type(build) is not dict
                    or build.keys() != {"scheme","sha256"} or build["scheme"] != "openpine.build-identity.1"
                    or type(build["sha256"]) is not str or len(build["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in build["sha256"])):
                raise ValueError("Invalid artifact identity scheme/build provenance.")
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
