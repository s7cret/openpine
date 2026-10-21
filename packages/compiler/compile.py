"""Source → syntax tree → one binding pass → typed execution IR."""

import hashlib
from dataclasses import fields, is_dataclass, replace

from packages.compiler.frontend.parser import Parser, Source, language_version
from packages.compiler.lowering import lower_program
from packages.compiler.semantic.binder import Binder, Catalog
from packages.compiler.semantic.specification import value_sha
from packages.contracts import Diagnostic, OpenPineError, ProgramIdentity, HISTORICAL_PROFILE_ID


def compile_source(text: str, name: str, catalog: Catalog, implementation_sha256: str,
                   implementation_profile: str):
    source = Source(text, name)
    if len(text.encode("utf-8")) > 65536:
        source.fail("PROFILE_UNCOVERED", "SOURCE_LIMIT", "Literal slice source limit is 64 KiB.", 0, 0)
    snapshot = catalog.snapshot
    grammar = snapshot.grammar
    version = language_version(source, grammar["directive_pattern"])
    snapshot.profile(version)
    if version != catalog.language_version:
        raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE", "CATALOG_VERSION", "Compiler catalog does not match the source version.", source.span(0, 0), language_version=version))
    semantic_profile, semantic_hash, notes = snapshot.semantic_identity(version)
    identity = ProgramIdentity(hashlib.sha256(text.encode("utf-8")).hexdigest(), version,
        snapshot.lock["manifest_sha256"], value_sha(snapshot.lock), semantic_hash, semantic_profile,
        implementation_profile, implementation_sha256, notes)
    try:
        statements = Parser(source, grammar,allow_strategy=bool(snapshot.strategies and implementation_profile==HISTORICAL_PROFILE_ID)).parse()
        binder = Binder(catalog)
        bound = binder.program(statements, source.span(0, 0))
        program = lower_program(bound, identity, binder.bindings, catalog.policy, binder.functions)
        from packages.contracts.context_ir import request_nodes
        request_nodes(program)
        from packages.contracts.strategy_ir import strategy_nodes
        strategy_nodes(program)
        pending, count = [(program, 0)], 0
        while pending:
            node, depth = pending.pop()
            if hasattr(node, "kind"):
                count += 1
                depth += 1
                if count > 8192 or depth > 128:
                    source.fail("PROFILE_UNCOVERED", "FUNCTION_EXPANSION", "Expanded IR exceeds its size/depth limit.", 0, 0)
            if is_dataclass(node):
                pending.extend((getattr(node, field.name), depth) for field in fields(node))
            elif isinstance(node, tuple):
                pending.extend((child, depth) for child in node)
        return program
    except OpenPineError as error:
        if error.diagnostic.language_version is None:
            raise OpenPineError(replace(error.diagnostic, language_version=version)) from error
        raise
