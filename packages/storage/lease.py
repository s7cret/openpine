"""Cross-platform ownership and publication locks."""

import os
import time
from pathlib import Path

from packages.contracts import Diagnostic, OpenPineError


class Lease:
    def __init__(self, path, *, wait_seconds=0):
        self.stream = Path(path).open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0"); self.stream.flush()
        deadline = time.monotonic() + wait_seconds
        while True:
            self.stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise OpenPineError(Diagnostic("INPUT_INVALID", "JOB_STORE_BUSY", "Another operation owns this directory."))
                time.sleep(.02)

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
