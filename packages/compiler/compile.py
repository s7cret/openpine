"""Source → syntax tree → one binding pass → typed execution IR."""

import hashlib
from dataclasses import replace

from packages.compiler.frontend.parser import Parser, Source, language_version
from packages.compiler.lowering import lower_program
from packages.compiler.semantic.binder import Binder, Catalog
from packages.compiler.semantic.specification import value_sha
from packages.contracts import Diagnostic, OpenPineError, ProgramIdentity


def compile_source(text: str, name: str, catalog: Catalog, implementation_sha256: str,
                   implementation_profile: str):
    source = Source(text, name)
    if len(text.encode("utf-8")) > 65536:
        source.fail("PROFILE_UNCOVERED", "SOURCE_LIMIT", "Literal slice source limit is 64 KiB.", 0, 0)
    snapshot = catalog.snapshot
    grammar = snapshot.grammar
    version = language_version(source, grammar["directive_pattern"])
    snapshot.profile(version)
    if version != 6:
        raise OpenPineError(Diagnostic("IMPLEMENTATION_MISSING", "LANGUAGE_PROFILE_UNIMPLEMENTED",
            f"Pine v{version} is recorded but not implemented in this execution slice.", source.span(0, 0),
            language_version=version))
    identity = ProgramIdentity(hashlib.sha256(text.encode("utf-8")).hexdigest(), version,
        snapshot.lock["manifest_sha256"], value_sha(snapshot.lock), snapshot.lock["semantic_hash"], snapshot.lock["semantic_profile_id"],
        implementation_profile, implementation_sha256)
    try:
        statements = Parser(source, grammar).parse()
        return lower_program(Binder(catalog).program(statements, source.span(0, 0)), identity)
    except OpenPineError as error:
        if error.diagnostic.language_version is None:
            raise OpenPineError(replace(error.diagnostic, language_version=version)) from error
        raise
