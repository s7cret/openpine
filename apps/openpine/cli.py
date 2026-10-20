"""Compile or run the bounded literal and historical Pine v6 profiles."""

import argparse
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path

from jsonschema.exceptions import ValidationError

from packages.compiler.semantic.specification import SpecError
from packages.contracts import Diagnostic, HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID, OpenPineError
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.engine import program_hash


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("compile", "run"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--profile", choices=("literal", "historical"), help="default: historical with --bars, otherwise literal")
    parser.add_argument("--bars", type=Path, help="confirmed historical close-only JSON input for run")
    parser.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() in {path.resolve() for path in (args.source, args.bars) if path}:
        parser.error("--output must not overwrite an input file")
    if args.bars and (args.command != "run" or args.profile == "literal"):
        parser.error("--bars requires run with the historical profile")
    historical = args.profile == "historical" or args.bars is not None
    exit_code = 0
    try:
        source = args.source.read_bytes().decode("utf-8")
        session = Session(profile_id=HISTORICAL_PROFILE_ID if historical else LITERAL_PROFILE_ID)
        if args.command == "compile":
            program = session.compile(source, args.source.as_posix())
            result = None
        else:
            data = read_bars(args.bars) if args.bars else None
            program, result = session.run(source, args.source.as_posix(), data=data)
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
        payload = {"status": "rejected", "diagnostics": [asdict(Diagnostic("INPUT_UNAVAILABLE", "INPUT_IO", str(error)))]}
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
