"""One complete release gate, with optional fast feedback and measured test shards."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from tools.specification import ROOT, require_python

FAST_MODULES = ('test_wire.py', 'test_optimizer.py', 'test_research_metrics.py', 'test_walkforward_contracts.py','test_protection.py','test_technical.py','test_composite.py','test_loops.py','test_arrays.py','test_environment.py','test_signals.py','test_context_graph.py','test_partial_broker.py','test_pending_entries.py','test_lots.py')
PRIORITY = ('test_specification.py', 'test_wire.py', 'test_hardening.py', 'test_live_market.py')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fast', action='store_true', help='development feedback; does not qualify a release')
    parser.add_argument('--timings', type=Path, help='directory for module and case timing JSON')
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.shard_count <= 16 or not 0 <= args.shard_index < args.shard_count:
        parser.error('Require 0 <= shard-index < shard-count <= 16.')
    if args.fast and args.shard_count != 1:parser.error('Fast feedback is not a release shard.')
    require_python()
    if sys.version_info[:3] != (3, 14, 8):
        print('The R6 gate requires Python 3.14.8.', file=sys.stderr)
        return 2
    checks = [('architecture', ('-m', 'tools.verify_architecture'))]
    if not args.fast:
        checks += [('specification', ('-m', 'tools.verify_spec'))]
        checks += [(name, ('-m', 'tools.'+name, '--check')) for name in
                   ('language_spec', 'series_spec', 'function_spec', 'request_spec', 'strategy_spec', 'exit_spec', 'technical_spec', 'composite_spec', 'loop_spec', 'array_spec', 'environment_spec', 'environment_fixtures', 'signal_spec', 'signal_fixtures', 'context_graph_fixtures', 'context_graph_examples', 'partial_exit_spec', 'partial_exit_fixtures', 'pending_entry_spec', 'pending_entry_fixtures', 'lot_spec', 'lot_fixtures', 'input_spec', 'replay_spec')]
        checks += [('conformance', ('-m', 'tools.conformance'))]
    modules = list(FAST_MODULES) if args.fast else [*PRIORITY, *sorted(p.name for p in (ROOT/'tests').glob('test_*.py') if p.name not in PRIORITY)]
    modules = [name for index, name in enumerate(modules) if index % args.shard_count == args.shard_index]
    if args.timings:
        args.timings = args.timings.resolve()
        args.timings.mkdir(parents=True, exist_ok=True)
    for name in modules:
        arguments = ['-m', 'tools.test_runner', '--module', name]
        if args.timings:arguments += ['--output', str(args.timings/(name+'.json'))]
        checks.append((name, tuple(arguments)))
    timings = []
    code = 0
    for name, arguments in checks:
        started = time.monotonic()
        result = subprocess.run([sys.executable, '-X', 'utf8', *arguments], cwd=ROOT, check=False)
        timings.append({'check': name, 'seconds': round(time.monotonic()-started, 6), 'exit_code': result.returncode})
        print(f"Check timing {name}: {timings[-1]['seconds']:.3f}s, exit {result.returncode}", flush=True)
        if result.returncode:code = result.returncode;break
    if args.timings:
        (args.timings/'gate.json').write_text(json.dumps({'fast': args.fast, 'shard_index': args.shard_index,
            'shard_count': args.shard_count, 'assigned_modules': modules, 'status': 'failed' if code else 'passed', 'checks': timings}, indent=2),
            encoding='utf-8', newline='\n')
    if not code:
        print('R6 fast feedback passed (not release qualification).' if args.fast else
              f'R6 gate passed on Python 3.14.8 (shard {args.shard_index+1}/{args.shard_count}).')
    return code


if __name__ == '__main__':raise SystemExit(main())
