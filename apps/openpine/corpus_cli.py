"""Batch compilation and finite worker-backed corpus runs."""
import argparse
from pathlib import Path
import sys
import time
import uuid

from apps.openpine.corpus import Corpus, Runs, compile_batch
from apps.openpine.bootstrap import local_manager
from packages.contracts.wire import atomic_json, canonical


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('catalogue','compile','run','report','list'))
    parser.add_argument('--case',action='append',dest='cases',help='Known case ID; repeat to select several (default: all 24).')
    parser.add_argument('--state-dir',type=Path,default=Path('.openpine'))
    parser.add_argument('--run-id',help='Report ID or idempotency key for run.')
    parser.add_argument('--parent-id',help='Prior run being explicitly rerun; evidence is preserved.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.command=='report' and not args.run_id:parser.error('report needs --run-id')
    if args.cases and args.command not in ('run','compile'):parser.error('--case needs run or compile')
    if args.parent_id and args.command!='run':parser.error('--parent-id needs run')
    try:
        corpus=Corpus()
        if args.output:
            target=args.output.resolve()
            protected={corpus.root/'examples/corpus/manifest.json'}|{corpus.root/r['path'] for c in corpus.cases.values() for r in c['files'].values()}
            if target in protected:raise ValueError('Output must not overwrite corpus input.')
        if args.command=='catalogue':value=corpus.catalogue()
        elif args.command=='compile':value=compile_batch(corpus,args.cases)
        else:
            with local_manager(args.state_dir,autostart=args.command=='run') as manager:
                runs=Runs(manager,corpus,recover=args.command=='run')
                try:
                    if args.command=='report':value=runs.report(args.run_id)
                    elif args.command=='list':value=runs.list()
                    else:
                        run_id=args.run_id or uuid.uuid4().hex
                        runs.create({'request_id':run_id,'case_ids':[c['id'] for c in corpus.select(args.cases)],
                                     **({'parent_id':args.parent_id} if args.parent_id else {})})
                        while True:
                            value=runs.report(run_id)
                            if value['report']['status']!='running':break
                            time.sleep(.1)
                finally:runs.close()
        code=2 if value.get('status')=='failed' or value.get('report',{}).get('status') in ('failed','interrupted','cancelled') else 0
    except (ValueError,OSError,KeyError) as error:
        value={'status':'rejected','code':'CORPUS_INVALID','message':str(error)};code=2
    if args.output:atomic_json(args.output,value)
    else:sys.stdout.write(canonical(value).decode('utf-8')+'\n')
    return code
