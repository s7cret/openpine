"""Build and install the wheel into an empty target, then run outside the checkout."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.build_backend import ROOT, build_wheel


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pip-runner", type=Path, help="official pip.pyz, for an embedded Python without pip")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="openpine-install-") as directory:
        work = Path(directory)
        wheel = work / build_wheel(work)
        site = work / "site"
        pip = [str(args.pip_runner.resolve())] if args.pip_runner else ["-m", "pip"]
        subprocess.run([sys.executable, "-X", "utf8", *pip, "--isolated", "--disable-pip-version-check",
            "install", "--no-index", "--no-deps", "--no-compile", "--no-warn-script-location",
            "--target", str(site), str(wheel)], cwd=work, check=True)
        bootstrap = "import runpy,sys;site=sys.argv.pop(1);old=sys.argv.pop(1);sys.path[:]=[site,*[p for p in sys.path if p and p!=old]];runpy.run_module('tools.installed_smoke',run_name='__main__')"
        subprocess.run([sys.executable, "-X", "utf8", "-c", bootstrap, str(site), str(ROOT),
            "--expected-root", str(site)], cwd=work, check=True, timeout=600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
