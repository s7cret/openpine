"""POSIX per-command wait4 metrics; no persistent environment modifications."""

import json
import importlib.util
import os
from pathlib import Path
import shutil
import sys

if __name__ == "__main__":
    metrics = Path(sys.argv[1])
    owner = Path(
        os.environ.get(
            "OPENPINE_PROCESS_OWNER",
            str(Path(__file__).resolve().parents[2] / "openpine/verification/execution_process.py"),
        )
    )
    spec = importlib.util.spec_from_file_location("verification_process_owner", owner)
    if spec is None or spec.loader is None:
        raise ValueError("declared process owner unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = sys.argv[2:]
    if not argv:
        raise ValueError("measured command is required")
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError(argv[0])
    child = module.start_declared_process([executable, *argv[1:]])
    _, status, usage = os.wait4(child.pid, 0)
    child.returncode = os.waitstatus_to_exitcode(status)
    metrics.write_text(
        json.dumps(
            {
                "user_cpu_s": usage.ru_utime,
                "system_cpu_s": usage.ru_stime,
                "max_rss_kib": usage.ru_maxrss,
            }
        )
        + "\n"
    )
    sys.exit(child.returncode)
