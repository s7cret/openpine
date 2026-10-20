"""The first local compile/run session; one synthetic observation."""

import hashlib
import json
import sys
from importlib.metadata import version

from packages.compiler.compile import compile_source
from packages.compiler.semantic.binder import Catalog
from packages.compiler.semantic.specification import ROOT, load_snapshot, require_python
from packages.runtime.engine import Runtime
from packages.runtime.operations import KERNELS

PROFILE_ID = "openpine.literal-harness.1"


def implementation_hash() -> str:
    files = {}
    for directory in ("packages", "apps"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"files": files, "python": list(sys.version_info[:3]), "jsonschema": version("jsonschema")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Session:
    def __init__(self, snapshot=None):
        require_python()
        self.snapshot = snapshot if snapshot is not None else load_snapshot()
        self.catalog = Catalog(self.snapshot, {name: kernel.parameters for name, kernel in KERNELS.items()})
        self.implementation_sha256 = implementation_hash()

    def compile(self, source: str, source_name: str = "<source>"):
        return compile_source(source, source_name, self.catalog, self.implementation_sha256, PROFILE_ID)

    def run(self, source: str, source_name: str = "<source>"):
        program = self.compile(source, source_name)
        runtime = Runtime(program.identity, self.catalog.contracts.values())
        return program, runtime.run(program)
