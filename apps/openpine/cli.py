"""Compile or run the first literal Pine v6 execution slice."""

import argparse
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path

from jsonschema.exceptions import ValidationError

from packages.compiler.semantic.specification import SpecError
from packages.contracts import Diagnostic, OpenPineError
from packages.execution.session import Session
from packages.runtime.engine import program_hash


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("compile", "run"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() == args.source.resolve():
        parser.error("--output must not overwrite the Pine source")
    exit_code = 0
    try:
        source = args.source.read_bytes().decode("utf-8")
        session = Session()
        if args.command == "compile":
            program = session.compile(source, args.source.as_posix())
            result = None
        else:
            program, result = session.run(source, args.source.as_posix())
        payload = {"status": "compiled" if result is None else "executed", "python": platform.python_version(),
                   "identity": asdict(program.identity), "program_sha256": program_hash(program),
                   "ir": asdict(program), "result": asdict(result) if result else None}
    except OpenPineError as error:
        payload = {"status": "rejected", "diagnostics": [asdict(error.diagnostic)]}
        exit_code = 2
    except (SpecError, ValidationError) as error:
        code = getattr(error, "code", "SPEC_INVALID")
        category = code if code in ("SPEC_UNRESOLVED", "PROFILE_UNCOVERED", "IMPLEMENTATION_MISSING") else "ARTIFACT_INCOMPATIBLE"
        payload = {"status": "rejected", "diagnostics": [asdict(Diagnostic(category, code, str(error)))]}
        exit_code = 2
    except (OSError, UnicodeError) as error:
        payload = {"status": "rejected", "diagnostics": [asdict(Diagnostic("INPUT_UNAVAILABLE", "SOURCE_IO", str(error)))]}
        exit_code = 2
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output:
        try:
            args.output.write_text(text, encoding="utf-8", newline="\n")
        except OSError as error:
            print(str(error), file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    return exit_code
