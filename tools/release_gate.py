"""Single gate for the current R6 specification stage."""

import subprocess
import sys

from tools.specification import ROOT, require_python


def main() -> int:
    require_python()
    if sys.version_info[:3] != (3, 14, 8):
        print("The R6 gate requires Python 3.14.8.", file=sys.stderr)
        return 2
    for arguments in (("-m", "tools.verify_spec"), ("-m", "unittest", "discover", "-s", "tests", "-v")):
        result = subprocess.run([sys.executable, "-X", "utf8", *arguments], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    print("R6 specification gate passed on Python 3.14.8.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
