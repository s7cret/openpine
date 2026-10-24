"""Compile once and supply confirmed historical bars in a single event order."""

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
from packages.contracts.loops import loop_policy
from packages.contracts.arrays import array_policy
from packages.execution.identity import implementation_hash
from packages.execution.driver import HistoricalDriver,MemorySource

PROFILE_ID = LITERAL_PROFILE_ID


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
        if not self.snapshot.loops or self.snapshot.loops['versions'][str(version)]['policy'] != loop_policy(version):
            raise SpecError('IMPLEMENTATION_MISSING', 'Loop policy is not implemented by this runtime')
        if not self.snapshot.arrays or self.snapshot.arrays['versions'][str(version)]['policy'] != array_policy(version):
            raise SpecError('IMPLEMENTATION_MISSING','Array policy is not implemented by this runtime')
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

    def run(self, source: str, source_name: str = "<source>", *, data=None,primary_context=None,context_sources=(),broker_settings=None,inputs=None):
        program = self.compile(source, source_name)
        return program, self.execute(program, data=data,primary_context=primary_context,context_sources=context_sources,broker_settings=broker_settings,inputs=inputs)

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

    def execute(self, program, *, data=None, progress=None, cancelled=None,primary_context=None,context_sources=(),broker_settings=None,inputs=None):
        runtime = self.validate_program(program)
        from packages.contracts.inputs import settings_for
        settings=settings_for(program,inputs)
        version = program.identity.language_version
        if self.profile_id == PROFILE_ID:
            if data is not None:
                raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "BAR_PROFILE", "Select the historical profile for bar input.", language_version=version))
            return runtime.run(program,settings=settings)
        if data is None:
            raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "BARS_REQUIRED", "Historical execution requires --bars input.", language_version=version))
        identity = data_identity(data)
        source=MemorySource(data)
        contexts=self.context_scheduler(program,runtime,source,primary_context,context_sources,settings=settings)
        from packages.contracts.strategy_ir import broker_policy
        driver=HistoricalDriver(runtime.prepare(program,settings=settings),source,contexts,broker_policy(program,broker_settings))
        driver.run(progress=progress,cancelled=cancelled)
        return driver.finish(identity)

    def execute_stream(self, program, source, sink, *, progress=None, cancelled=None):
        if self.profile_id != HISTORICAL_PROFILE_ID:
            raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "BAR_PROFILE", "Streaming requires historical profile."))
        driver=self.stream_driver(program,source,sink)
        driver.run(progress=progress,cancelled=cancelled)
        return driver.finish_stream(source.identity)

    def stream_driver(self,program,source,sink,*,primary_context=None,context_sources=(),simulation_policy=None,settings=None,evaluation_window=None):
        runtime=self.validate_program(program)
        contexts=self.context_scheduler(program,runtime,source,primary_context,context_sources,settings=settings)
        from packages.contracts.strategy_ir import broker_policy
        policy=broker_policy(program,simulation_policy.settings if simulation_policy else None)
        if simulation_policy is not None and policy!=simulation_policy:raise ValueError('Compiled strategy differs from the pinned broker policy.')
        return HistoricalDriver(runtime.prepare(program,output_sink=sink,settings=settings),source,contexts,policy,evaluation_window)

    def context_scheduler(self,program,runtime,source,primary,context_sources,*,settings=None):
        from packages.contracts.context_ir import request_nodes
        if primary is None and not context_sources and not request_nodes(program):return None
        if hasattr(source,"bind_identity"):source.bind_identity()
        from packages.execution.contexts import ContextScheduler
        return ContextScheduler(program,runtime,primary,source,context_sources,settings=settings)
