"""A deliberate ordinary-Python cwd shadow must fail specifically on origin."""

import json
from pathlib import Path
import subprocess
import sys
from openpine.verification.execution_process import start_declared_process

if __name__ == "__main__":
    poison, probe, output = map(Path, sys.argv[1:4])
    child = start_declared_process(
        [
            sys.executable,
            "-c",
            "import runpy,sys;sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')",
            str(probe),
            "origins",
            "--output",
            str(output),
        ],
        cwd=poison,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = child.communicate()
    raw = {
        "argv": child.args,
        "cwd": str(poison),
        "exit_code": child.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    assert child.returncode != 0
    assert "source-shadowing" in stderr and str(poison / "openpine" / "__init__.py") in stderr, (
        "unrelated failure is not a shadowing rejection"
    )
    assert not output.exists()
    output.with_name("shadow-child.json").write_text(
        json.dumps(raw, sort_keys=True, indent=2) + "\n"
    )
    assert child.returncode == 1
    print(
        json.dumps(
            {
                "source_shadowing_rejected": True,
                "child_exit_code": child.returncode,
                "shadow_module": "openpine",
            },
            sort_keys=True,
        )
    )
