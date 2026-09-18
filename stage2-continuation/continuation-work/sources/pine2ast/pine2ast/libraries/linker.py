"""AST-anchored static linking of same-language Pine libraries.

Only resolved identifier occurrences are edited, never arbitrary source strings.
The linked virtual source is parsed and type-checked again by the normal frontend.
Original inputs and projection ranges remain available in the linkage receipt.

This revision admits typed functions, UDTs, enums, helpers and constant globals.
Nominal type names include the locked publication identity; type inference remains
owned by the normal frontend. Methods are projected through the existing typed method resolver, with explicit
module visibility. Mixed Pine versions and request expressions remain separate profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
import json
import re
from typing import Mapping, NoReturn

from pine2ast.api import ParseOptions, ParsePipeline
from pine2ast.ast.base import ASTNode
from pine2ast.ast.nodes import (
    Block,
    CallExpr,
    EnumDeclaration,
    ForInStructure,
    ForRangeStructure,
    FunctionDeclaration,
    Identifier,
    ImportDeclaration,
    Literal,
    MemberAccessExpr,
    MethodDeclaration,
    Program,
    Reassignment,
    TupleDeclaration,
    TypeDeclaration,
    VarDeclaration,
)
from pine2ast.ast.visitors import walk
from pine2ast.ast.types import TypeRef
from pine2ast.catalog import CatalogRepository
from pine2ast.lexer.token import TokenKind
from .store import (
    LibraryError,
    LibraryStore,
    MAX_DEPTH,
    MAX_SOURCE_BYTES,
    canonical,
    source_hash,
    valid_ref,
)

PREFIX = "__p2a_library_"
SCALARS = {"int", "float", "bool", "string", "color"}
LINK_SCHEMA = "pine2ast.linked_libraries.v1"


@dataclass(frozen=True, slots=True)
class LinkedSource:
    code: str
    _receipt: bytes

    def receipt(self) -> dict:
        value = json.loads(self._receipt)
        body = {k: v for k, v in value.items() if k != "content_hash"}
        if (
            source_hash(canonical(body)) != value["content_hash"]
            or source_hash(self.code) != value["linked_source_hash"]
        ):
            raise LibraryError("P2A_LIBRARY_PROJECTION", "linked source or projection was changed")
        return value

    def verify(self) -> None:
        """Reproduce the complete projection; a self-rehashed receipt is not proof."""
        value = self.receipt()
        if value.get("schema_id") != LINK_SCHEMA or value.get("profile") not in {
            "same_version_scalar_v1",
            "same_version_arrays_v2",
            "same_version_collections_v3",
            "same_version_reference_types_v4",
            "same_version_methods_v5",
            "same_version_function_overloads_v6",
            "same_version_mixed_callables_v7",
        }:
            raise LibraryError("P2A_LIBRARY_PROJECTION", "unsupported linkage profile")
        root_name = value.get("root_source_name")
        if not isinstance(root_name, str):
            raise LibraryError("P2A_LIBRARY_PROJECTION", "root source name must be text")
        try:
            root = value["sources"][root_name]["raw_text"]
            sources = {ref: value["sources"][ref]["raw_text"] for ref in value["dependencies"]}
            if sources:
                rebuilt = link_libraries(root, LibraryStore.create(sources), source_name=root_name)
            else:
                raise LibraryError("P2A_LIBRARY_PROJECTION", "linked source needs dependencies")
        except (KeyError, TypeError) as exc:
            raise LibraryError("P2A_LIBRARY_PROJECTION", "incomplete linkage provenance") from exc
        if rebuilt.code != self.code or rebuilt._receipt != self._receipt:
            raise LibraryError("P2A_LIBRARY_PROJECTION", "linkage differs from original sources")

    @property
    def dependency_hashes(self) -> dict[str, str]:
        return dict(self.receipt()["dependencies"])

    def qualifier_context(self):
        """Derive exported-result provenance from the verified locked projection."""
        from .qualifier_context import LibraryQualifierContext

        return LibraryQualifierContext.from_linked_source(self)

    def original_locations(self, offsets) -> list[dict | None]:
        """Project many character offsets with one validated receipt and indexed lookup.

        Coordinates refer to normalized Pine text, not UTF-8 bytes. Generated-only
        spans have no original location. Renamed tokens map to their original start.
        """
        from bisect import bisect_right

        values = list(offsets)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("offset must be a nonnegative integer")
        receipt = self.receipt()
        rows = receipt["projection"]
        starts = [row["generated_start"] for row in rows]
        lines = {}
        result = []
        for offset in values:
            index = bisect_right(starts, offset) - 1
            if index < 0 or offset >= rows[index]["generated_end"]:
                result.append(None)
                continue
            row = rows[index]
            same = row["source_end"] - row["source_start"] == row["generated_end"] - row["generated_start"]
            original = row["source_start"] + (offset - row["generated_start"] if same else 0)
            source = row["source"]
            if source not in lines:
                text = receipt["sources"][source]["text"]
                lines[source] = [0] + [i + 1 for i, char in enumerate(text) if char == "\n"]
            line = bisect_right(lines[source], original)
            result.append({"source": source, "offset": original, "line": line,
                           "column": original - lines[source][line - 1] + 1})
        return result

    def original_location(self, offset: int) -> dict | None:
        return self.original_locations([offset])[0]


@dataclass(slots=True)
class _Unit:
    ref: str
    text: str
    program: Program
    tokens: list
    imports: dict[str, str]
    functions: dict[str, FunctionDeclaration]
    methods: dict[str, MethodDeclaration]
    function_groups: dict[str, tuple[str, ...]]
    method_names: frozenset[str]
    constants: dict[str, VarDeclaration]
    types: dict[str, TypeDeclaration | EnumDeclaration]
    globals: set[str]
    exports: set[str]
    renamed: dict[str, str]


def _parse(ref: str, text: str) -> _Unit:
    options = ParseOptions(
        source_name=ref, run_semantic=False, collect_tokens=True, created_at_utc_ms=0
    )
    pipeline = ParsePipeline(options)
    normalized = pipeline.normalize(text)
    result = pipeline.parse(normalized.text)
    if not result.ok or result.ast is None:
        detail = "; ".join(d.message for d in result.diagnostics if d.is_error)
        first = next((d for d in result.diagnostics if d.is_error), None)
        raise LibraryError(
            "P2A_LIBRARY_SYNTAX", detail, source=ref,
            line=first.span.start_line if first else None,
            column=first.span.start_col if first else None,
        )
    program = result.ast
    declarations, functions, constants, exports, imports = set(), {}, {}, set(), {}
    function_counts = {}
    for node in program.items:
        if isinstance(node, FunctionDeclaration):
            function_counts[node.name] = function_counts.get(node.name, 0) + 1
    method_names = {n.name for n in program.items if isinstance(n, MethodDeclaration)}
    callable_names = set()
    function_groups = {}
    types = {}
    methods = {}
    for item in program.items:
        if isinstance(item, ImportDeclaration):
            path = valid_ref(item.path)
            alias = item.alias or item.library
            if not alias or alias in imports or alias in declarations:
                raise LibraryError(
                    "P2A_LIBRARY_ALIAS",
                    "duplicate or invalid import alias",
                    source=ref,
                    line=item.span.start_line,
                )
            imports[alias] = path
        elif isinstance(
            item,
            (
                FunctionDeclaration,
                MethodDeclaration,
                VarDeclaration,
                TypeDeclaration,
                EnumDeclaration,
            ),
        ):
            same_function_family = isinstance(item, (FunctionDeclaration, MethodDeclaration)) and item.name in callable_names
            if item.name in imports or (item.name in declarations and not same_function_family):
                raise LibraryError(
                    "P2A_LIBRARY_NAME",
                    "duplicate module-level name",
                    source=ref,
                    line=item.span.start_line,
                )
            declarations.add(item.name)
            if isinstance(item, (FunctionDeclaration, MethodDeclaration)):
                callable_names.add(item.name)
            key = (f"@method:{item.name}:{item.span.start_offset}"
                   if isinstance(item, MethodDeclaration) else
                   f"@function:{item.name}:{item.span.start_offset}"
                   if isinstance(item, FunctionDeclaration) and (function_counts[item.name] > 1 or item.name in method_names)
                   else item.name)
            if getattr(item, "is_exported", False):
                exports.add(key)
            if isinstance(item, MethodDeclaration):
                methods[key] = item
            elif isinstance(item, FunctionDeclaration):
                functions[key] = item
                function_groups.setdefault(item.name, []).append(key)
            elif isinstance(item, VarDeclaration):
                constants[item.name] = item
            elif isinstance(item, (TypeDeclaration, EnumDeclaration)):
                types[item.name] = item
        elif isinstance(item, TupleDeclaration):
            for target in item.targets:
                if target.name != "_":
                    declarations.add(target.name)
    # Aliases may not overwrite existing builtin namespaces or declaration names.
    catalog = CatalogRepository.default().readonly_view(program.version_context.pine_version)
    namespaces = {
        name.split(".")[0]
        for cat in catalog.values()
        if isinstance(cat, Mapping)
        for name in cat
        if isinstance(name, str) and "." in name
    }
    if set(imports) & namespaces:
        raise LibraryError(
            "P2A_LIBRARY_ALIAS", "import alias conflicts with a builtin namespace", source=ref
        )
    for node in walk(program):
        name = getattr(node, "name", None)
        if isinstance(name, str) and name.startswith(PREFIX):
            raise LibraryError(
                "P2A_LIBRARY_RESERVED",
                "reserved linker identifier",
                source=ref,
                line=node.span.start_line,
            )
        if isinstance(node, ImportDeclaration) and node not in program.items:
            raise LibraryError(
                "P2A_LIBRARY_IMPORT_SCOPE",
                "imports must be module-level",
                source=ref,
                line=node.span.start_line,
            )
    return _Unit(
        ref,
        normalized.text,
        program,
        result.tokens or [],
        imports,
        functions,
        methods,
        {name: tuple(keys) for name, keys in function_groups.items()},
        frozenset(method.name for method in methods.values()),
        constants,
        types,
        declarations,
        exports,
        {},
    )


class _Linker:
    def __init__(self, root: str, store: LibraryStore, source_name: str):
        self.root = _parse(source_name, root)
        self.root_raw = root
        self.profile = "same_version_scalar_v1"
        self.store = store
        self.units: dict[str, _Unit] = {}
        self.version = self.root.program.version_context.pine_version
        self.explicit_method_calls: dict[tuple[str, int, int], str] = {}
        self.explicit_function_calls: dict[tuple[str, int, int], str] = {}
        self.selected: set[tuple[str, str]] = set()
        self.visiting: list[tuple[str, str]] = []
        self.order: list[tuple[str, str]] = []
        self.edits: dict[str, dict[tuple[int, int], str]] = {}
        catalog = CatalogRepository.default().readonly_view(self.version)
        self.builtins = {
            name
            for category in catalog.values()
            if isinstance(category, Mapping)
            for name in category
            if isinstance(name, str)
        }
        self.namespaces = {name.split(".")[0] for name in self.builtins if "." in name}

    def promote_profile(self, profile: str) -> None:
        profiles = ("same_version_scalar_v1", "same_version_arrays_v2", "same_version_collections_v3", "same_version_reference_types_v4", "same_version_methods_v5", "same_version_function_overloads_v6", "same_version_mixed_callables_v7")
        if profiles.index(profile) > profiles.index(self.profile):
            self.profile = profile

    def fail(self, unit: _Unit, node: ASTNode, code: str, message: str) -> NoReturn:
        raise LibraryError(code, message, source=unit.ref, line=node.span.start_line)

    def _admit_version_invariant_unit(self, unit) -> bool:
        from .version_compatibility import (
            VersionSensitiveLibrary, prove_version_invariant_library,
        )
        try:
            proof = prove_version_invariant_library(unit.text)
        except VersionSensitiveLibrary:
            return False
        proofs = getattr(self, "_version_invariant_proofs", None)
        if proofs is None:
            proofs = self._version_invariant_proofs = {}
        proofs[proof["source_hash"]] = proof
        return True

    def load(self, ref: str, stack: tuple[str, ...] = ()) -> None:
        if ref in stack:
            raise LibraryError("P2A_LIBRARY_CYCLE", " -> ".join((*stack, ref)))
        if len(stack) >= MAX_DEPTH:
            raise LibraryError("P2A_LIBRARY_LIMIT", "dependency depth exceeded")
        if ref in self.units:
            return
        text = self.store.source(ref)
        unit = _parse(ref, text)
        if unit.program.declaration is None or unit.program.declaration.script_type != "library":
            raise LibraryError(
                "P2A_LIBRARY_DECLARATION", "dependency must declare library()", source=ref
            )
        if (unit.program.version_context.pine_version != self.version) and not self._admit_version_invariant_unit(unit):
            raise LibraryError(
                "P2A_LIBRARY_VERSION_CONTEXT",
                "mixed Pine-language versions need separate evaluation; not admitted by this linker",
                source=ref,
            )
        call = unit.program.declaration.call
        title = next(
            (a.value for a in call.arguments if a.name == "title"),
            call.arguments[0].value if call.arguments else None,
        )
        if (
            not isinstance(title, Literal)
            or title.literal_type != "string"
            or title.value != ref.split("/")[1]
        ):
            raise LibraryError(
                "P2A_LIBRARY_TITLE",
                "library title must match the locked publication name",
                source=ref,
            )
        if not unit.exports:
            raise LibraryError(
                "P2A_LIBRARY_EXPORT", "library must export at least one declaration", source=ref
            )
        if unit.exports - (set(unit.functions) | set(unit.types) | set(unit.methods)):
            raise LibraryError(
                "P2A_LIBRARY_EXPORT_SHAPE",
                "this link profile supports typed functions, methods, UDTs and enums",
                source=ref,
            )
        unit.renamed = {
            name: PREFIX + source_hash((ref + "\0" + text))[7:27] + "_" + name
            for name in unit.functions | unit.constants | unit.types
            if not name.startswith("@function:")
        }
        unit.renamed.update({
            key: PREFIX + source_hash(ref + "\0" + text + "\0function:" + key)[7:27] + "_" + node.name
            for key, node in unit.functions.items() if key.startswith("@function:")
        })
        # Each resolved method declaration gets its own private name. Keeping
        # overloads under one spelling would let the final semantic pass select
        # a private/more-specific overload that was invisible in the preview.
        unit.renamed.update({
            key: PREFIX + source_hash((ref + "\0" + text + "\0method:" + key))[7:27] + "_" + node.name
            for key, node in unit.methods.items()
        })
        # Resolve every declared edge, even when calls under it are not selected.
        self.units[ref] = unit
        for child in sorted(set(unit.imports.values())):
            self.load(child, (*stack, ref))
        for name in sorted(unit.exports & (unit.functions.keys() | unit.methods.keys())):
            function = unit.functions.get(name) or unit.methods[name]
            parameters = list(function.parameters)
            if isinstance(function, MethodDeclaration):
                from pine2ast.ast.nodes import Parameter
                parameters.insert(0, Parameter(function.span, function.receiver_name or "",
                    function.receiver_type, function.receiver_explicit_qualifier))
            for parameter in parameters:
                if (
                    parameter.type_ref is None
                    or not (
                        self.public_type_allowed(unit, parameter.type_ref)
                        or (
                            parameter.type_ref.name in {"array", "matrix", "map"}
                            and len(parameter.type_ref.template_args) == (2 if parameter.type_ref.name == "map" else 1)
                            and all(self.public_type_allowed(unit, t) for t in parameter.type_ref.template_args)
                            and parameter.explicit_qualifier != "simple"
                        )
                    )
                    or parameter.explicit_qualifier not in {None, "simple", "series"}
                ):
                    self.fail(
                        unit,
                        parameter,
                        "P2A_LIBRARY_PARAMETER",
                        "export requires a declared public value or series collection parameter",
                    )
                if parameter.type_ref.name == "array":
                    self.promote_profile("same_version_arrays_v2")
                elif parameter.type_ref.name in {"map", "matrix"}:
                    self.promote_profile("same_version_collections_v3")
        for name in sorted(unit.exports & set(unit.types)):
            declaration = unit.types[name]
            if isinstance(declaration, TypeDeclaration):
                for field in declaration.fields:
                    for node in walk(field.type_ref):
                        if isinstance(node, TypeRef):
                            resolved = self.resolve_type(unit, node.name)
                            if resolved is not None and resolved[1] not in resolved[0].exports:
                                self.fail(unit, field, "P2A_LIBRARY_PRIVATE_TYPE", "exported field exposes private type: " + node.name)

    def resolve_type(self, unit: _Unit, name: str):
        if name in unit.types:
            return unit, name
        alias, dot, member = name.partition(".")
        if dot and alias in unit.imports:
            target = self.units[unit.imports[alias]]
            if member in target.types:
                if member not in target.exports:
                    raise LibraryError("P2A_LIBRARY_PRIVATE", "imported type is private: " + member, source=target.ref)
                return target, member
        return None

    def public_type_allowed(self, unit: _Unit, type_ref: TypeRef) -> bool:
        if type_ref.template_args:
            return False
        if type_ref.name in SCALARS:
            return True
        resolved = self.resolve_type(unit, type_ref.name)
        if resolved is None:
            return False
        target, name = resolved
        if name not in target.exports:
            self.fail(unit, type_ref, "P2A_LIBRARY_PRIVATE_TYPE", "public signature exposes private type: " + name)
        return True

    def visit_type(self, unit: _Unit, type_ref: TypeRef, *, public: bool = False) -> None:
        resolved = self.resolve_type(unit, type_ref.name)
        if resolved is not None:
            target, name = resolved
            self.require(target.ref, name, public=public or target is not unit)
            self.promote_profile("same_version_reference_types_v4")
            # Type names are syntax nodes, not identifier expressions. Preserve
            # generic brackets, member/parameter names and arbitrary whitespace.
            tokens = [t for t in unit.tokens if type_ref.span.start_offset <= t.span.start_offset < type_ref.span.end_offset]
            spelling = ""
            for token in tokens:
                spelling += token.text
                if spelling == type_ref.name:
                    self.edit(unit, tokens[0].span.start_offset, token.span.end_offset, target.renamed[name])
                    break
            else:
                self.fail(unit, type_ref, "P2A_LIBRARY_PROJECTION", "type token not found")
        for child in type_ref.template_args:
            self.visit_type(unit, child, public=public)

    def edit(self, unit: _Unit, start: int, end: int, text: str) -> None:
        edits = self.edits.setdefault(unit.ref, {})
        key = (start, end)
        if key in edits and edits[key] != text:
            raise LibraryError("P2A_LIBRARY_PROJECTION", "conflicting identifier projection")
        edits[key] = text

    def name_edit(self, unit: _Unit, node: ASTNode, name: str) -> None:
        # Declaration names are not Identifier AST children; bind the exact lexer
        # token before its parameter list / initializer, never global replace().
        until = (
            node.initializer.span.start_offset
            if isinstance(node, VarDeclaration)
            else node.span.end_offset
        )
        matches = [
            t
            for t in unit.tokens
            if t.kind == TokenKind.IDENTIFIER
            and t.text == name
            and node.span.start_offset <= t.span.start_offset < until
        ]
        if not matches:
            self.fail(unit, node, "P2A_LIBRARY_PROJECTION", "declaration token not found")
        token = matches[0]
        self.edit(unit, token.span.start_offset, token.span.end_offset, unit.renamed[name])

    def require(self, ref: str, name: str, *, public: bool = False) -> None:
        unit = self.units[ref]
        if public and name not in unit.exports:
            raise LibraryError(
                "P2A_LIBRARY_PRIVATE", "imported member is not exported: " + name, source=ref
            )
        key = (ref, name)
        if key in self.visiting:
            if name in unit.types:
                return  # Recursive reference fields do not recursively execute.
            raise LibraryError(
                "P2A_LIBRARY_RECURSION", "recursive function/constant dependency", source=ref
            )
        if key in self.selected:
            return
        declaration = unit.functions.get(name) or unit.methods.get(name) or unit.constants.get(name) or unit.types.get(name)
        if declaration is None:
            raise LibraryError(
                "P2A_LIBRARY_MEMBER", "unresolved library member: " + name, source=ref
            )
        self.visiting.append(key)
        if isinstance(declaration, VarDeclaration):
            if declaration.mode or declaration.explicit_qualifier not in {None, "const"}:
                self.fail(
                    unit,
                    declaration,
                    "P2A_LIBRARY_CAPTURE",
                    "library function cannot capture a non-const global",
                )
            if any(
                isinstance(n, Reassignment)
                and isinstance(n.target, Identifier)
                and n.target.name == name
                for item in unit.program.items
                if not isinstance(item, (FunctionDeclaration, MethodDeclaration))
                for n in walk(item)
            ):
                self.fail(unit, declaration, "P2A_LIBRARY_CAPTURE", "global constant is reassigned")
            # Admit only syntax known to be a scalar constant, not parent-scope data.
            allowed = {"Literal", "Identifier", "UnaryExpr", "BinaryExpr", "ConditionalExpr"}
            if any(n.kind not in allowed for n in walk(declaration.initializer)):
                self.fail(
                    unit,
                    declaration,
                    "P2A_LIBRARY_CAPTURE",
                    "global initializer is not a supported constant expression",
                )
            self.visit(unit, declaration.initializer, [], constant_only=True)
        elif isinstance(declaration, TypeDeclaration):
            self.promote_profile("same_version_reference_types_v4")
            for field in declaration.fields:
                self.visit_type(unit, field.type_ref, public=declaration.is_exported)
                if field.default_value is not None:
                    self.visit(unit, field.default_value, [])
        elif isinstance(declaration, EnumDeclaration):
            self.promote_profile("same_version_reference_types_v4")
        else:
            self.visit_function(unit, declaration)
        if not isinstance(declaration, MethodDeclaration) and not name.startswith("@function:"):
            self.name_edit(unit, declaration, name)
        self.visiting.pop()
        self.selected.add(key)
        self.order.append(key)

    def visit_function(self, unit: _Unit, function: FunctionDeclaration | MethodDeclaration) -> None:
        names = set(p.name for p in function.parameters)
        if isinstance(function, MethodDeclaration):
            if function.receiver_type is not None:
                self.visit_type(unit, function.receiver_type, public=function.is_exported)
            if function.receiver_name:
                names.add(function.receiver_name)
        for parameter in function.parameters:
            if parameter.type_ref is not None:
                self.visit_type(unit, parameter.type_ref, public=function.is_exported)
            if parameter.default_value is not None:
                self.visit(unit, parameter.default_value, [])
        self.visit(unit, function.body, [names])

    def visit(
        self, unit: _Unit, node: ASTNode, scopes: list[set[str]], *, constant_only: bool = False
    ) -> None:
        def local(name):
            return any(name in s for s in reversed(scopes))

        type_ref = getattr(node, "type_ref", None)
        if isinstance(type_ref, TypeRef):
            self.visit_type(unit, type_ref)
        if isinstance(node, TypeRef):
            self.visit_type(unit, node)
            return

        if isinstance(node, CallExpr):
            callee = node.callee
            target = None
            if isinstance(callee, MemberAccessExpr) and isinstance(callee.object, Identifier):
                alias = callee.object.name
                if alias in unit.imports and not local(alias):
                    owner = self.units[unit.imports[alias]]
                    if len(owner.function_groups.get(callee.member, ())) > 1 or (callee.member in owner.function_groups and callee.member in owner.method_names):
                        self.explicit_function_calls[(unit.ref, callee.span.start_offset, callee.span.end_offset)] = owner.ref
                        if callee.member in owner.method_names:
                            self.explicit_method_calls[(unit.ref, callee.span.start_offset, callee.span.end_offset)] = owner.ref
                        for argument in node.arguments:
                            self.visit(unit, argument.value, scopes)
                        return
                    if callee.member not in owner.function_groups and callee.member in owner.method_names:
                        target = owner
                        self.explicit_method_calls[(
                            unit.ref, callee.span.start_offset, callee.span.end_offset
                        )] = owner.ref
            if (isinstance(callee, Identifier) and not local(callee.name)
                    and (len(unit.function_groups.get(callee.name, ())) > 1
                         or (callee.name in unit.function_groups and callee.name in unit.method_names))):
                if constant_only:
                    self.fail(unit, node, "P2A_LIBRARY_CAPTURE", "function call is not a constant initializer")
                for argument in node.arguments:
                    self.visit(unit, argument.value, scopes)
                return
            bare_method = (
                isinstance(callee, Identifier) and not local(callee.name)
                and callee.name not in unit.function_groups
                and callee.name in unit.method_names
            )
            if target is not None or bare_method:
                if constant_only:
                    self.fail(unit, node, "P2A_LIBRARY_CAPTURE", "method call is not a constant initializer")
                # Selection is deferred to the shared typed preview; do not
                # guess overloads or manufacture function wrapper declarations.
                for argument in node.arguments:
                    self.visit(unit, argument.value, scopes)
                return

        if isinstance(node, MemberAccessExpr):
            if isinstance(node.object, Identifier) and not local(node.object.name):
                alias = node.object.name
                if alias in unit.imports:
                    if constant_only:
                        self.fail(
                            unit,
                            node,
                            "P2A_LIBRARY_CAPTURE",
                            "library call is not a constant initializer",
                        )
                    target = self.units[unit.imports[alias]]
                    self.require(target.ref, node.member, public=True)
                    self.edit(
                        unit,
                        node.span.start_offset,
                        node.span.end_offset,
                        target.renamed[node.member],
                    )
                    return
            if constant_only:
                self.fail(
                    unit,
                    node,
                    "P2A_LIBRARY_CAPTURE",
                    "only literal/arithmetic constant globals are admitted",
                )
        if isinstance(node, Identifier):
            name = node.name
            if local(name):
                return
            if name in unit.imports:
                self.fail(
                    unit,
                    node,
                    "P2A_LIBRARY_ALIAS_VALUE",
                        "library alias must qualify an exported declaration",
                )
            if unit is not self.root:
                if name in unit.globals:
                    if constant_only and name not in unit.constants:
                        self.fail(unit, node, "P2A_LIBRARY_CAPTURE", "not a scalar constant")
                    self.require(unit.ref, name)
                    self.edit(
                        unit, node.span.start_offset, node.span.end_offset, unit.renamed[name]
                    )
                elif constant_only or (name not in self.builtins and name not in self.namespaces):
                    self.fail(
                        unit, node, "P2A_LIBRARY_UNBOUND", "unresolved library identifier: " + name
                    )
            return
        if isinstance(node, CallExpr) and unit is not self.root:
            from pine2ast.semantic.type_infer import callee_name

            name = callee_name(node.callee) or ""
            if name.startswith("array."):
                self.promote_profile("same_version_arrays_v2")
            elif name.startswith(("map.", "matrix.")):
                self.promote_profile("same_version_collections_v3")
            if (
                name == "input"
                or name.startswith(("input.", "request.", "strategy."))
                or name == "security"
            ):
                self.fail(
                    unit,
                    node,
                    "P2A_LIBRARY_CALL_PROFILE",
                    "unsupported call in imported function: " + name,
                )
            if name in {
                "plot",
                "plotshape",
                "plotchar",
                "hline",
                "fill",
                "indicator",
                "strategy",
                "library",
                "alertcondition",
            }:
                self.fail(
                    unit,
                    node,
                    "P2A_LIBRARY_LOCAL_CALL",
                    "global-only call inside imported function",
                )
        if isinstance(node, Block):
            scope: set[str] = set()
            for statement in node.statements:
                self.visit(unit, statement, [*scopes, scope], constant_only=constant_only)
                if isinstance(statement, VarDeclaration):
                    scope.add(statement.name)
                elif isinstance(statement, TupleDeclaration):
                    scope.update(t.name for t in statement.targets)
            return
        if isinstance(node, (FunctionDeclaration, MethodDeclaration)):
            self.visit_function(unit, node)
            return
        if isinstance(node, ForRangeStructure):
            for field in fields(node):
                value = getattr(node, field.name)
                if isinstance(value, ASTNode):
                    self.visit(
                        unit, value, [*scopes, {node.variable}] if field.name == "body" else scopes
                    )
            return
        if isinstance(node, ForInStructure):
            if unit is not self.root:
                self.promote_profile("same_version_arrays_v2")
            self.visit(unit, node.iterable, scopes)
            self.visit(unit, node.body, [*scopes, set(node.target.names)])
            return
        if isinstance(node, Reassignment) and unit is not self.root:
            if isinstance(node.target, Identifier) and not local(node.target.name):
                self.fail(
                    unit,
                    node,
                    "P2A_LIBRARY_CAPTURE",
                    "cannot reassign a library global from a function",
                )
        if not is_dataclass(node):
            self.fail(unit, node, "P2A_LIBRARY_PROJECTION", "invalid AST structure")
        for field in fields(node):
            if field.name in {
                "span",
                "documentation",
                "type_ref",
                "annotations",
                "producer_metadata",
                "diagnostics",
            }:
                continue
            value = getattr(node, field.name)
            if isinstance(value, ASTNode):
                self.visit(unit, value, scopes, constant_only=constant_only)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, ASTNode):
                        self.visit(unit, child, scopes, constant_only=constant_only)

    def render(self, identity: str) -> tuple[str, list[dict]]:
        code, projection = [], []
        length = 0

        def literal(text):
            nonlocal length
            code.append(text)
            length += len(text)

        def chunk(unit, start, end):
            nonlocal length
            cursor = start
            edits = sorted(
                (a, b, v)
                for (a, b), v in self.edits.get(unit.ref, {}).items()
                if start <= a and b <= end
            )
            for a, b, replacement in [*edits, (end, end, "")]:
                if a < cursor:
                    raise LibraryError("P2A_LIBRARY_PROJECTION", "overlapping identifier ranges")
                for original_start, original_end, text in (
                    (cursor, a, unit.text[cursor:a]),
                    (a, b, replacement),
                ):
                    if text:
                        projection.append(
                            {
                                "generated_start": length,
                                "generated_end": length + len(text),
                                "source": unit.ref,
                                "source_start": original_start,
                                "source_end": original_end,
                            }
                        )
                        literal(text)
                cursor = b

        end = self.root.program.declaration.span.end_offset
        chunk(self.root, 0, end)
        literal("\n// openpine-library-link: " + identity + "\n")
        for ref, name in self.order:
            unit = self.units[ref]
            item = unit.functions.get(name) or unit.methods.get(name) or unit.constants.get(name) or unit.types[name]
            chunk(unit, item.span.start_offset, item.span.end_offset)
            literal("\n")
        chunk(self.root, end, len(self.root.text))
        joined = "".join(code)
        if len(joined.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise LibraryError("P2A_LIBRARY_LIMIT", "linked source exceeds size limit")
        return joined, projection

    def run(self) -> LinkedSource:
        if self.version not in {5, 6}:
            raise LibraryError("P2A_LIBRARY_VERSION", "library imports require Pine v5 or v6")
        if self.root.program.declaration is None:
            raise LibraryError("P2A_LIBRARY_DECLARATION", "consumer must have a script declaration")
        for ref in sorted(set(self.root.imports.values())):
            self.load(ref)
        function_overloads = any(any(len(keys) > 1 or name in unit.method_names for name, keys in unit.function_groups.items())
                                 for unit in self.units.values())
        if function_overloads:
            self.promote_profile("same_version_function_overloads_v6")
            for ref, unit in sorted(self.units.items()):
                for name, keys in unit.function_groups.items():
                    if len(keys) > 1 or name in unit.method_names:
                        for key in keys:
                            self.require(ref, key)
        # A nominal library's public interface must be valid independently of
        # which export this consumer calls. Include its exported functions in
        # normal frontend inference so an unused private-type return cannot
        # escape validation. Defining a function does not execute example code.
        for ref, unit in sorted(self.units.items()):
            if unit.types:
                for name in sorted(unit.exports & unit.functions.keys()):
                    self.require(ref, name, public=True)
        methods_present = any(unit.methods for unit in self.units.values())
        if methods_present:
            self.promote_profile("same_version_methods_v5")
            for ref, unit in sorted(self.units.items()):
                for key in unit.methods:
                    self.require(ref, key)
        for item in self.root.program.items:
            if isinstance(item, ImportDeclaration):
                self.edit(self.root, item.span.start_offset, item.span.end_offset, "")
            else:
                self.visit(self.root, item, [])
        if any(set(u.function_groups) & u.method_names for u in (self.root, *self.units.values())):
            self.promote_profile("same_version_mixed_callables_v7")
        closure = {ref: source_hash(self.store.source(ref)) for ref in sorted(self.units)}
        identity = source_hash(
            canonical(
                {
                    "profile": self.profile,
                    "root": source_hash(self.root_raw),
                    "libraries": closure,
                }
            )
        )
        joined, projection = self.render(identity)
        if methods_present or function_overloads:
            from .method_projection import project_methods
            project_methods(self, joined, projection)
            joined, projection = self.render(identity)
        if self.profile in {"same_version_reference_types_v4", "same_version_methods_v5", "same_version_function_overloads_v6", "same_version_mixed_callables_v7"}:
            # Use the existing semantic owner to infer public return types after
            # projection; private helpers may use private types internally.
            parsed = ParsePipeline(ParseOptions(created_at_utc_ms=0)).parse(joined)
            symbols = getattr(parsed.semantic_model, "symbols", {})
            private_types = {
                unit.renamed[name]
                for unit in self.units.values()
                for name in unit.types.keys() - unit.exports
            }
            for ref, name in self.order:
                unit = self.units[ref]
                if name in (unit.functions.keys() | unit.methods.keys()) and name in unit.exports:
                    symbol = symbols.get(unit.renamed[name])
                    if name in unit.methods:
                        owner = getattr(parsed.semantic_model, "method_candidates", None)
                        if owner is not None:
                            from .method_projection import _Visibility
                            method = unit.methods[name]
                            locations = _Visibility(self, projection)
                            for candidate in owner.candidates:
                                if (candidate.declaration.name == unit.renamed[name]
                                        and locations.declaration(candidate.declaration)[1] == name):
                                    symbol = symbols.get(candidate.symbol_key)
                                    rt = getattr(symbol, "type", "") or ""
                                    if private_types & set(re.findall(r"[A-Za-z_]\w*", rt)):
                                        self.fail(unit, method, "P2A_LIBRARY_PRIVATE_TYPE", "exported return exposes a private type")
                    return_type = getattr(symbol, "type", "") or ""
                    if private_types & set(re.findall(r"[A-Za-z_]\w*", return_type)):
                        self.fail(unit, unit.functions.get(name) or unit.methods[name], "P2A_LIBRARY_PRIVATE_TYPE", "exported return exposes a private type")
        sources = {
            u.ref: {
                "text": u.text,
                "sha256": source_hash(u.text),
                "raw_text": self.root_raw if u is self.root else self.store.source(u.ref),
            }
            for u in [self.root, *self.units.values()]
        }
        receipt = {
            **({"version_compatibility": {"policy": "proven_scalar_invariance_v1", "proofs": dict(sorted(self._version_invariant_proofs.items()))}} if getattr(self, "_version_invariant_proofs", None) else {}),
            "schema_id": LINK_SCHEMA,
            "profile": self.profile,
            "pine_version": self.version,
            "root_source_name": self.root.ref,
            "root_source_hash": source_hash(self.root_raw),
            "linked_source_hash": source_hash(joined),
            "closure_hash": identity,
            "dependencies": closure,
            "sources": sources,
            "projection": projection,
            "declarations": [
                {"ref": r, "name": n, "linked_name": self.units[r].renamed[n]}
                for r, n in self.order
            ],
        }
        receipt["content_hash"] = source_hash(canonical(receipt))
        return LinkedSource(joined, canonical(receipt))


def link_libraries(
    source: str, store: LibraryStore, *, source_name: str = "<memory>"
) -> LinkedSource:
    """Resolve exact revisions and perform lexical module lowering before type admission."""
    if not isinstance(store, LibraryStore):
        raise LibraryError("P2A_LIBRARY_LOCK", "an admitted LibraryStore is required")
    # Even a manually constructed dataclass cannot bypass admission or retain mutable inputs.
    detached = LibraryStore.admit(store.lock(), store._sources, expected_hash=store.content_hash)
    if source_name in detached._sources:
        raise LibraryError("P2A_LIBRARY_SOURCE", "root source name collides with a library ref")
    return _Linker(source, detached, source_name).run()


def has_library_imports(source: str, *, source_name: str = "<memory>") -> bool:
    """Lexical dispatch only; import text in strings/comments is not a declaration."""
    tokens, _ = ParsePipeline(ParseOptions(source_name=source_name)).lex_only(source)
    return any(token.kind == TokenKind.IMPORT for token in tokens)
