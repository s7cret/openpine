"""Compile once and supply confirmed historical bars in a single event order."""

import hashlib
import json
import sys
from importlib.metadata import version

from packages.compiler.compile import compile_source
from packages.compiler.frontend.parser import Source, language_version
from packages.compiler.semantic.binder import Catalog
from packages.compiler.semantic.specification import ROOT, SpecError, load_snapshot, require_python, value_sha
from packages.contracts import BarFrame, Diagnostic, HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID, OpenPineError, ValueType
from packages.marketdata import data_identity
from packages.runtime.engine import Runtime
from packages.runtime.operations import KERNELS
from packages.runtime.series import supported_rules
from packages.runtime.language import language_policy
from packages.runtime.history import series_policy
from packages.runtime.functions import function_policy

PROFILE_ID = LITERAL_PROFILE_ID


def implementation_hash() -> str:
    files = {}
    for directory in ("packages", "apps"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"files": files, "python": list(sys.version_info[:3]), "jsonschema": version("jsonschema"),
        "apsw": version("apsw"), "duckdb": version("duckdb")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Session:
    def __init__(self, snapshot=None, *, profile_id=PROFILE_ID):
        require_python()
        if profile_id not in (PROFILE_ID, HISTORICAL_PROFILE_ID):
            raise ValueError("Unknown implementation profile")
        self.profile_id = profile_id
        self.snapshot = snapshot if snapshot is not None else load_snapshot()
        self.capabilities = {name: (kernel.parameters, *kernel.aliases) for name, kernel in KERNELS.items()
                             if profile_id == HISTORICAL_PROFILE_ID or "updates_state" not in kernel.effects}
        self.catalogs = {}
        self.catalog = self.catalog_for(6)  # Compatibility accessor for callers inspecting the original slice.
        self.implementation_sha256 = implementation_hash()

    def catalog_for(self, version):
        if version in self.catalogs:
            return self.catalogs[version]
        catalog = Catalog(self.snapshot, self.capabilities,
            {slot: slot for slot in ("open", "high", "low", "close", "volume", "bar_index")} if self.profile_id == HISTORICAL_PROFILE_ID else {}, version)
        if not self.snapshot.series or self.snapshot.series["versions"][str(version)]["policy"] != series_policy(version):
            raise SpecError("IMPLEMENTATION_MISSING", "Series policy is not implemented by this runtime")
        if not self.snapshot.functions or self.snapshot.functions["versions"][str(version)]["policy"] != function_policy(version):
            raise SpecError("IMPLEMENTATION_MISSING", "Function policy is not implemented by this runtime")
        if catalog.policy != language_policy(version):
            raise SpecError("IMPLEMENTATION_MISSING", "Statement policy is not implemented by this runtime")
        if self.profile_id == HISTORICAL_PROFILE_ID:
            rules = {row["rule_id"]: row for row in self.snapshot.registry_for(version)["rules"]}
            for rule_id, supported in supported_rules(version).items():
                if rule_id not in rules:
                    raise SpecError("SPEC_UNRESOLVED", f"Required runtime rule is missing: {rule_id}")
                admitted = self.snapshot.resolve(rules[rule_id]["semantic_assertion"], version)
                if admitted != supported:
                    raise SpecError("IMPLEMENTATION_MISSING", f"Kernel does not implement the admitted rule: {rule_id}")
            if catalog.references["close"].value_type != ValueType("float", "series"):
                raise SpecError("IMPLEMENTATION_MISSING", "Unsupported close value contract")
        self.catalogs[version] = catalog
        return catalog

    def compile(self, source: str, source_name: str = "<source>"):
        version = language_version(Source(source, source_name), self.snapshot.grammar["directive_pattern"])
        return compile_source(source, source_name, self.catalog_for(version), self.implementation_sha256, self.profile_id)

    def run(self, source: str, source_name: str = "<source>", *, data=None):
        program = self.compile(source, source_name)
        return program, self.execute(program, data=data)

    def validate_program(self, program):
        identity = program.identity
        version = identity.language_version
        if type(version) is not int or version not in range(1, 7):
            raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE", "ARTIFACT_VERSION", "Invalid Pine version."))
        semantic_profile, semantic_hash, notes = self.snapshot.semantic_identity(version)
        expected = (self.snapshot.lock["manifest_sha256"], value_sha(self.snapshot.lock), semantic_hash,
                    semantic_profile, self.profile_id, self.implementation_sha256, notes)
        actual = (identity.spec_manifest_sha256, identity.spec_lock_sha256, identity.semantic_sha256,
                  identity.semantic_profile_id, identity.implementation_profile_id, identity.implementation_sha256,
                  identity.compatibility_notes)
        if actual != expected or len(identity.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in identity.source_sha256):
            raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE", "ARTIFACT_IDENTITY", "Artifact specification, profile or implementation differs from this installation."))
        catalog = self.catalog_for(version)
        runtime = Runtime(identity, catalog.contracts.values(), catalog.references.values())
        runtime.admit(program)
        return runtime

    def execute(self, program, *, data=None, progress=None, cancelled=None):
        runtime = self.validate_program(program)
        version = program.identity.language_version
        if self.profile_id == PROFILE_ID:
            if data is not None:
                raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "BAR_PROFILE", "Select the historical profile for bar input.", language_version=version))
            return runtime.run(program)
        if data is None:
            raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "BARS_REQUIRED", "Historical execution requires --bars input.", language_version=version))
        identity = data_identity(data)
        evaluation = runtime.prepare(program)
        for index, bar in enumerate(data.bars):
            if cancelled and cancelled():
                raise OpenPineError(Diagnostic("EXECUTION_CANCELLED", "CANCELLED", "Execution cancelled."))
            evaluation.step(BarFrame(data.context_id, index, bar.time, bar.close, bar.open, bar.high, bar.low, bar.volume))
            if progress and (index == len(data.bars) - 1 or index % 100 == 0):
                progress(index + 1, len(data.bars))
        return evaluation.finish(identity)

    def execute_stream(self, program, source, sink, *, progress=None, cancelled=None):
        if self.profile_id != HISTORICAL_PROFILE_ID:
            raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "BAR_PROFILE", "Streaming requires historical profile."))
        runtime = self.validate_program(program)
        evaluation = runtime.prepare(program, output_sink=sink)
        for index, bar in enumerate(source):
            if cancelled and cancelled():
                raise OpenPineError(Diagnostic("EXECUTION_CANCELLED", "CANCELLED", "Execution cancelled."))
            evaluation.step(BarFrame(source.identity.context_id, index, bar.time, bar.close,
                bar.open, bar.high, bar.low, bar.volume))
            if progress and (index % 1000 == 0 or index + 1 == source.identity.count):
                progress(index + 1, source.identity.count)
        return evaluation.finish_stream(source.identity)
