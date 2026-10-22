"""Isolated unittest module with per-case timings, including failures."""
import argparse
import json
from pathlib import Path
import time
import unittest


class TimedResult(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append({'test': test.id(), 'seconds': round(time.monotonic()-self.started, 6)})
        super().stopTest(test)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not args.module.startswith('test_') or not args.module.endswith('.py') or Path(args.module).name != args.module:
        parser.error('Use one test module filename.')
    suite = unittest.defaultTestLoader.discover('tests', pattern=args.module)
    class Result(TimedResult):timings = []
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    for row in sorted(result.timings, key=lambda row: row['seconds'], reverse=True)[:3]:
        print(f"Timing {row['seconds']:.3f}s {row['test']}", flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'module': args.module, 'seconds': round(time.monotonic()-started, 6),
                              'tests': result.testsRun, 'successful': result.wasSuccessful(), 'cases': result.timings}, indent=2),
                              encoding='utf-8', newline='\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':raise SystemExit(main())
