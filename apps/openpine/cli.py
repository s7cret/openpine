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
from packages.execution.artifacts import read_program
from packages.marketdata import read_bars


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='trades':
        from apps.openpine.trades_cli import main as trades_main
        return trades_main(argv[1:])
    if argv and argv[0]=='walkforward':
        from apps.openpine.walkforward_cli import main as walkforward_main
        return walkforward_main(argv[1:])
    if argv and argv[0]=='search':
        from apps.openpine.optimization_cli import main as search_main
        return search_main(argv[1:])
    if argv and argv[0]=='experiment':
        from apps.openpine.experiment_cli import main as experiment_main
        return experiment_main(argv[1:])
    if argv and argv[0]=='market':
        from apps.openpine.market import main as market_main
        return market_main(argv[1:])
    if argv and argv[0] == 'replay':
        from apps.openpine.replay import main as replay_main
        return replay_main(argv[1:])
    if argv and argv[0] == "storage":
        from apps.openpine.storage import main as storage_main
        return storage_main(argv[1:])
    if argv and argv[0] == "job":
        from apps.openpine.jobs import main as job_main
        return job_main(argv[1:])
    if argv and argv[0] == "fetch":
        from apps.openpine.fetch import main as fetch_main
        return fetch_main(argv[1:])
    if argv and argv[0] == "serve":
        from apps.ui.server import main as serve_main
        return serve_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__, epilog="Other commands: job (isolated run), fetch (historical snapshot), serve (local web application), storage (catalog/import/cache/backup/restore). Use COMMAND --help.")
    parser.add_argument("command", choices=("compile", "run"))
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", type=Path)
    inputs.add_argument("--artifact", type=Path, help="run a saved compilation without recompiling")
    parser.add_argument("--profile", choices=("literal", "historical"), help="default: historical with --bars, otherwise literal")
    parser.add_argument("--bars", type=Path, help="confirmed historical close-only (.1) or OHLCV (.2) JSON input")
    parser.add_argument('--inputs',type=Path,help='JSON object of input overrides; run only')
    parser.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if args.inputs and args.command!='run':parser.error('--inputs requires run.')
    if args.output and args.output.resolve() in {path.resolve() for path in (args.source, args.artifact, args.bars,args.inputs) if path}:
        parser.error("--output must not overwrite an input file")
    if args.bars and (args.command != "run" or args.profile == "literal"):
        parser.error("--bars requires run with the historical profile")
    if args.artifact and (args.command != "run" or args.profile):
        parser.error("--artifact requires run and uses the artifact's profile")
    historical = args.profile == "historical" or args.bars is not None
    exit_code = 0
    try:
        from packages.storage.trade_index import TradeIndex
        app = Application(trade_history_factory=TradeIndex)
        from packages.contracts.wire import read_json
        input_values=read_json(args.inputs) if args.inputs else None
        if args.artifact:
            payload = app.execute(read_program(args.artifact), data=read_bars(args.bars) if args.bars else None,inputs=input_values)
        else:
            with args.source.open("rb") as stream:
                source = stream.read(65537).decode("utf-8")
            compilation = CompileRequest(source, args.source.as_posix(), HISTORICAL_PROFILE_ID if historical else LITERAL_PROFILE_ID)
            request = compilation if args.command == "compile" else RunRequest(compilation, read_bars(args.bars) if args.bars else None,input_values)
            payload = app.handle(request)
    except (OpenPineError, SpecError, ValidationError, OSError, UnicodeError) as error:
        payload = rejection(error)
    except (ValueError,TypeError) as error:
        payload={'status':'rejected','diagnostics':[{'category':'INPUT_INVALID','code':'INPUT_SETTINGS','message':str(error)}]}
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
