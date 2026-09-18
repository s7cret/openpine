"""Conservative proof of version-invariant Pine v5/v6 library declarations.

This is NOT a Pine converter or a replacement for normal semantic admission.
Only a closed, stateless scalar-expression language is admitted here. The real
producer still parses, types, and binds the original declarations. Version-sensitive
constructs are refused; they must use a future per-module semantic execution domain.
The proof is always regenerated from exact locked source bytes when linking.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

SCHEMA = "pine2ast.version_invariant_scalar_proof.v1"
IDENT = r"[A-Za-z_][A-Za-z_0-9]*"
PARAM = re.compile(rf"(?:(simple|series)\s+)?(int|float|string)\s+({IDENT})\Z")
DECL = re.compile(rf"(export\s+)?({IDENT})\s*\(([^()]*)\)\s*=>\s*(.+)\Z")

class VersionSensitiveLibrary(ValueError):
    """A source was not proven invariant; it must never be silently converted."""

@dataclass(frozen=True)
class _Function:
    name: str
    exported: bool
    params: tuple[tuple[str, str, str], ...]
    expression: ast.expr
    line: int


def _without_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif quote and char == "\\":
            escaped = True
        elif quote and char == quote:
            quote = None
        elif not quote and char in ("'", '"'):
            quote = char
        elif not quote and line[index:index+2] == "//":
            return line[:index].strip()
    return line.strip()


def _canonical(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def prove_version_invariant_library(source: str) -> dict[str, Any]:
    """Return a reproducible structural proof or raise; no source is evaluated.

    Allowed: single-line, non-overloaded int/float/string functions, typed
    parameters, literal values, + - * %, unary numeric signs, and acyclic calls
    to other admitted functions in the same unit. No division, comparisons,
    bool, NA literal, history, persistent state, globals, builtins or imports.
    Library declarations and untouched source bytes remain in the ordinary linker.
    """
    if not isinstance(source, str):
        raise TypeError("library source must be text")
    versions = re.findall(r"(?m)^\s*//@version\s*=\s*(\d+)\s*$", source)
    if len(versions) != 1 or versions[0] not in ("5", "6"):
        raise VersionSensitiveLibrary("one explicit Pine v5/v6 directive required")
    functions: dict[str, _Function] = {}
    seen_header = False
    for line_no, raw in enumerate(source.splitlines(), 1):
        line = _without_comment(raw)
        if not line:
            continue
        if not seen_header:
            try:
                header = ast.parse(line, mode="eval").body
            except SyntaxError as exc:
                raise VersionSensitiveLibrary("simple library declaration required") from exc
            if not (isinstance(header, ast.Call) and isinstance(header.func, ast.Name)
                    and header.func.id == "library" and len(header.args) == 1
                    and isinstance(header.args[0], ast.Constant)
                    and type(header.args[0].value) is str and not header.keywords):
                raise VersionSensitiveLibrary("only library(\"name\") is in this proof profile")
            seen_header = True
            continue
        match = DECL.fullmatch(line)
        if not match or raw[:1].isspace():
            raise VersionSensitiveLibrary(f"line {line_no}: non-scalar or multiline declaration")
        exported, name, args, expression = match.groups()
        if name in functions:
            raise VersionSensitiveLibrary("overloads need per-module semantic admission")
        params = []
        for arg in args.split(",") if args.strip() else []:
            parameter = PARAM.fullmatch(arg.strip())
            if not parameter:
                raise VersionSensitiveLibrary("only explicitly typed scalar parameters; no defaults")
            qualifier, typ, pname = parameter.groups()
            if any(pname == p[0] for p in params):
                raise VersionSensitiveLibrary("duplicate parameter")
            params.append((pname, typ, qualifier or "inferred"))
        try:
            tree = ast.parse(expression, mode="eval").body
        except SyntaxError as exc:
            raise VersionSensitiveLibrary("not a common scalar expression") from exc
        functions[name] = _Function(name, bool(exported), tuple(params), tree, line_no)
    if not seen_header or not functions or not any(f.exported for f in functions.values()):
        raise VersionSensitiveLibrary("no exported scalar functions")
    return_types: dict[str, str] = {}
    visiting: set[str] = set()
    calls: dict[str, set[str]] = {name: set() for name in functions}

    def function_type(name: str) -> str:
        if name in return_types:
            return return_types[name]
        if name in visiting:
            raise VersionSensitiveLibrary("recursive library call graph")
        visiting.add(name)
        function = functions[name]
        env = {pname: typ for pname, typ, _ in function.params}

        def infer(node: ast.expr) -> str:
            if isinstance(node, ast.Constant):
                typ = type(node.value)
                if typ is bool or node.value is None:
                    raise VersionSensitiveLibrary("bool and NA require versioned semantics")
                if typ is int and -(2**63) <= node.value <= 2**63-1:
                    return "int"
                if typ is float and math.isfinite(node.value):
                    return "float"
                if typ is str:
                    return "string"
                raise VersionSensitiveLibrary("unsupported scalar literal")
            if isinstance(node, ast.Name):
                if node.id not in env:
                    raise VersionSensitiveLibrary("globals and untyped names are not invariant")
                return env[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                typ = infer(node.operand)
                if typ not in ("int", "float"):
                    raise VersionSensitiveLibrary("numeric unary operands required")
                return typ
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Mod)):
                left, right = infer(node.left), infer(node.right)
                if isinstance(node.op, ast.Add) and left == right == "string":
                    return "string"
                if left not in ("int", "float") or right not in ("int", "float"):
                    raise VersionSensitiveLibrary("non-numeric arithmetic")
                return "float" if "float" in (left, right) else "int"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                target = node.func.id
                if target not in functions:
                    raise VersionSensitiveLibrary("builtin/external calls require per-module contracts")
                callee = functions[target]
                if node.keywords or len(node.args) != len(callee.params):
                    raise VersionSensitiveLibrary("only exact positional local calls in invariant profile")
                for value, (_, expected, _) in zip(node.args, callee.params):
                    actual = infer(value)
                    if actual != expected and not (actual == "int" and expected == "float"):
                        raise VersionSensitiveLibrary("local call scalar type mismatch")
                calls[name].add(target)
                return function_type(target)
            raise VersionSensitiveLibrary(f"{type(node).__name__} requires a semantic language domain")

        result = infer(function.expression)
        visiting.remove(name)
        return_types[name] = result
        return result

    for name in functions:
        function_type(name)
    result = {
        "schema_id": SCHEMA,
        "source_hash": "sha256:" + hashlib.sha256(source.encode()).hexdigest(),
        "source_pine_version": int(versions[0]),
        "compatible_consumer_versions": [5, 6],
        "proof_kind": "closed_stateless_scalar_expression_subset",
        "functions": {name: {"exported": function.exported,
            "parameters": [list(p) for p in function.params],
            "return_type": return_types[name],
            "expression": ast.dump(function.expression, include_attributes=False),
            "calls": sorted(calls[name])} for name, function in sorted(functions.items())},
        "not_full_mixed_version_support": True,
    }
    result["content_hash"] = _canonical(result)
    return result


def verify_version_invariant_proof(source: str, proof: dict[str, Any]) -> None:
    """A hash alone is insufficient: reproduce the proof from the original source."""
    if not isinstance(proof, dict) or prove_version_invariant_library(source) != proof:
        raise VersionSensitiveLibrary("version-invariant proof does not match exact source")
