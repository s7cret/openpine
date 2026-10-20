"""Compile once and supply confirmed historical bars in a single event order."""

import hashlib
import json
import sys
from importlib.metadata import version

from packages.compiler.compile import compile_source
from packages.compiler.semantic.binder import Catalog
from packages.compiler.semantic.specification import ROOT, SpecError, load_snapshot, require_python
from packages.contracts import BarFrame, Diagnostic, HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID, OpenPineError, ValueType
from packages.marketdata import data_identity
from packages.runtime.engine import Runtime
from packages.runtime.operations import KERNELS
from packages.runtime.series import SUPPORTED_RULES

PROFILE_ID = LITERAL_PROFILE_ID


def implementation_hash() -> str:
    files = {}
    for directory in ("packages", "apps"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"files": files, "python": list(sys.version_info[:3]), "jsonschema": version("jsonschema")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Session:
    def __init__(self, snapshot=None, *, profile_id=PROFILE_ID):
        require_python()
        if profile_id not in (PROFILE_ID, HISTORICAL_PROFILE_ID):
            raise ValueError("Unknown implementation profile")
        self.profile_id = profile_id
        self.snapshot = snapshot if snapshot is not None else load_snapshot()
        capabilities = {name: kernel.parameters for name, kernel in KERNELS.items()
                        if profile_id == HISTORICAL_PROFILE_ID or name != "series.ema"}
        self.catalog = Catalog(self.snapshot, capabilities, {"close": "close"} if profile_id == HISTORICAL_PROFILE_ID else {})
        if profile_id == HISTORICAL_PROFILE_ID:
            rules = {row["rule_id"]: row for row in self.snapshot.registry["rules"]}
            for rule_id, supported in SUPPORTED_RULES.items():
                if rule_id not in rules:
                    raise SpecError("SPEC_UNRESOLVED", f"Required runtime rule is missing: {rule_id}")
                admitted = self.snapshot.resolve(rules[rule_id]["semantic_assertion"], 6)
                if admitted != supported:
                    raise SpecError("IMPLEMENTATION_MISSING", f"Kernel does not implement the admitted rule: {rule_id}")
            if self.catalog.references["close"].value_type != ValueType("float", "series"):
                raise SpecError("IMPLEMENTATION_MISSING", "Unsupported close value contract")
        self.implementation_sha256 = implementation_hash()

    def compile(self, source: str, source_name: str = "<source>"):
        return compile_source(source, source_name, self.catalog, self.implementation_sha256, self.profile_id)

    def run(self, source: str, source_name: str = "<source>", *, data=None):
        program = self.compile(source, source_name)
        runtime = Runtime(program.identity, self.catalog.contracts.values(), self.catalog.references.values())
        if self.profile_id == PROFILE_ID:
            if data is not None:
                raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "BAR_PROFILE", "Select the historical profile for bar input.", language_version=6))
            return program, runtime.run(program)
        if data is None:
            raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "BARS_REQUIRED", "Historical execution requires --bars input.", language_version=6))
        identity = data_identity(data)
        evaluation = runtime.prepare(program)
        for index, bar in enumerate(data.bars):
            evaluation.step(BarFrame(data.context_id, index, bar.time, bar.close))
        return program, evaluation.finish(identity)
