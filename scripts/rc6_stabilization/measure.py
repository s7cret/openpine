"""POSIX per-command wait4 metrics; no persistent environment modifications."""
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    metrics = Path(sys.argv[1])
    child = subprocess.Popen(sys.argv[2:])
    _, status, usage = os.wait4(child.pid, 0)
    child.returncode = os.waitstatus_to_exitcode(status)
    metrics.write_text(json.dumps({'user_cpu_s': usage.ru_utime, 'system_cpu_s': usage.ru_stime, 'max_rss_kib': usage.ru_maxrss})+'\n')
    sys.exit(child.returncode)
