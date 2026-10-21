"""Compile or run bounded, explicitly qualified Pine v1-v6 profiles."""

import argparse
import json
import sys
from pathlib import Path

from jsonschema.exceptions import ValidationError

from packages.compiler.semantic.specification import SpecError
from packages.contracts import HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID, OpenPineError
from packages.contracts.application import CompileRequest, RunRequest
from packages.execution.service import Application, rejection
from packages.marketdata import read_bars


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("compile", "run"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--profile", choices=("literal", "historical"), help="default: historical with --bars, otherwise literal")
    parser.add_argument("--bars", type=Path, help="confirmed historical close-only (.1) or OHLCV (.2) JSON input")
    parser.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() in {path.resolve() for path in (args.source, args.bars) if path}:
        parser.error("--output must not overwrite an input file")
    if args.bars and (args.command != "run" or args.profile == "literal"):
        parser.error("--bars requires run with the historical profile")
    historical = args.profile == "historical" or args.bars is not None
    exit_code = 0
    try:
        with args.source.open("rb") as stream:
            source = stream.read(65537).decode("utf-8")
        compilation = CompileRequest(source, args.source.as_posix(), HISTORICAL_PROFILE_ID if historical else LITERAL_PROFILE_ID)
        if args.command == "compile":
            request = compilation
        else:
            data = read_bars(args.bars) if args.bars else None
            request = RunRequest(compilation, data)
        payload = Application().handle(request)
    except (OpenPineError, SpecError, ValidationError, OSError, UnicodeError) as error:
        payload = rejection(error)
    exit_code = 2 if payload["status"] == "rejected" else 0
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
