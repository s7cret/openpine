"""Offline program explanation; never downloads candles or submits a job."""
import argparse
from pathlib import Path
import sys
from apps.openpine.passport import explain,rejected
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.compiler.semantic.specification import SpecError
from packages.contracts.wire import read_json,canonical,atomic_json
from packages.execution.artifacts import read_program
from packages.execution.session import Session

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sources=parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--source',type=Path);sources.add_argument('--artifact',type=Path)
    parser.add_argument('--source-name',help='Stable diagnostic name when reading a source file.')
    parser.add_argument('--inputs',type=Path);parser.add_argument('--primary-context',type=Path)
    parser.add_argument('--options',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.source_name and not args.source:parser.error('--source-name requires --source')
    if args.output and args.output.resolve() in {p.resolve() for p in (args.source,args.artifact,args.inputs,args.primary_context,args.options) if p}:parser.error('Output must not overwrite an input.')
    stage='input'
    try:
        overrides=read_json(args.inputs) if args.inputs else None
        primary=read_json(args.primary_context) if args.primary_context else None
        options=read_json(args.options) if args.options else None
        session=Session(profile_id=HISTORICAL_PROFILE_ID);stage='compile'
        if args.artifact:program=read_program(args.artifact)
        else:
            with args.source.open('rb') as stream:raw=stream.read(65537)
            if len(raw)>65536:raise ValueError('Source exceeds 64 KiB.')
            program=session.compile(raw.decode('utf-8'),args.source_name or args.source.as_posix())
        value=explain(program,session=session,inputs=overrides,primary_context=primary,options=options)
    except (OpenPineError,SpecError,ValueError,OSError,UnicodeError) as error:value=rejected(error,stage)
    if args.output:atomic_json(args.output,value)
    else:sys.stdout.write(canonical(value).decode('utf-8')+'\n')
    return 2 if value['status']=='rejected' else 0

if __name__=='__main__':raise SystemExit(main())
