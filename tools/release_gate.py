"""Single gate for the specification and first R6 execution slice."""

import subprocess
import sys

from tools.specification import ROOT, require_python


def main() -> int:
    require_python()
    if sys.version_info[:3] != (3, 14, 8):
        print("The R6 gate requires Python 3.14.8.", file=sys.stderr)
        return 2
    for arguments in (("-m", "tools.verify_architecture"), ("-m", "tools.verify_spec"), ("-m", "tools.language_spec", "--check"),
                      ("-m", "tools.series_spec", "--check"),
                      ("-m", "tools.function_spec", "--check"),
                      ("-m", "tools.request_spec", "--check"),
                      ("-m", "tools.strategy_spec", "--check"),
                      ("-m", "tools.replay_spec", "--check"),
                      *(("-m", "unittest", "discover", "-s", "tests", "-p", path.name, "-v")
                        for path in sorted((ROOT/'tests').glob('test_*.py'))),
                      ("-m", "tools.conformance")):
        result = subprocess.run([sys.executable, "-X", "utf8", *arguments], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    print("R6 gate passed on Python 3.14.8.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
