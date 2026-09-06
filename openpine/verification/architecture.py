"""Static cross-component ownership gates, not a proof of semantic equivalence."""

from __future__ import annotations

import ast
import hashlib
from collections import Counter
from pathlib import Path

from openpine.verification.identity import digest, seal

COMPONENTS = {
    "openpine-contracts": ("openpine_contracts", (), "schemas and canonical identities"),
    "pine2ast": ("pine2ast", (), "Pine syntax, versions, types and catalog"),
    "ast2python": ("ast2python", ("pine2ast",), "IR, lowering and exact target binding"),
    "pinelib": ("pinelib", (), "language, series, builtins and request evaluation"),
    "marketdata-provider": (
        "marketdata_provider",
        ("openpine-contracts",),
        "data, finality and calendars",
    ),
    "backtest_engine": (
        "backtest_engine",
        ("openpine-contracts", "pinelib", "marketdata-provider"),
        "fills, sizing and ledger",
    ),
    "optimizer": (
        "optimizer",
        ("openpine-contracts", "backtest_engine"),
        "search and trial contract",
    ),
    "openpine": (
        "openpine",
        (
            "openpine-contracts",
            "pine2ast",
            "ast2python",
            "pinelib",
            "marketdata-provider",
            "backtest_engine",
            "optimizer",
        ),
        "orchestration, admission, storage and UX",
    ),
}
# Guard known semantic entrypoints against accidental copies. This does not claim
# to detect every possible reimplementation under arbitrary different names.
SYMBOL_OWNERS = {
    "resolve_engine_config": ("openpine", "openpine/runtime/rc6_config.py"),
    "serialize_engine_config": ("openpine", "openpine/runtime/rc6_config.py"),
    "process_bar_fills": ("backtest_engine", "backtest_engine/core/fill_scanner.py"),
    "resolve_exit_prices": ("backtest_engine", "backtest_engine/core/exit_prices.py"),
}


def check_architecture(stack: Path, *, host_root: Path | None = None) -> dict:
    owners = {spec[0]: name for name, spec in COMPONENTS.items()}
    issues, edges, inventory = [], Counter(), {}
    for component, (package, allowed, responsibility) in COMPONENTS.items():
        component_root = (
            host_root if component == "openpine" and host_root is not None else stack / component
        )
        root = component_root / package
        if not root.is_dir():
            issues.append({"code": "MISSING_COMPONENT", "component": component})
            continue
        files = sorted(root.rglob("*.py"))
        if not files:
            issues.append({"code": "EMPTY_COMPONENT", "component": component})
        inventory[component] = {
            "owner": responsibility,
            "files": len(files),
            "source_hash": digest(
                {
                    path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in files
                    if not path.is_symlink()
                }
            ),
        }
        for path in files:
            relative = path.relative_to(component_root).as_posix()
            if path.is_symlink():
                issues.append({"code": "SYMLINK_SOURCE", "component": component, "path": relative})
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeError) as error:
                issues.append(
                    {
                        "code": "INVALID_SOURCE",
                        "component": component,
                        "path": relative,
                        "detail": str(error),
                    }
                )
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                elif (
                    isinstance(node, ast.Call)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    # Literal dynamic imports are checked too. Arbitrary computed
                    # import strings require runtime sandbox checks, not guessing.
                    func = node.func
                    name = (
                        func.id
                        if isinstance(func, ast.Name)
                        else func.attr
                        if isinstance(func, ast.Attribute)
                        else ""
                    )
                    if name in {"import_module", "__import__"} and isinstance(
                        node.args[0].value, str
                    ):
                        names = [node.args[0].value]
                for name in names:
                    target = owners.get(name.split(".", 1)[0])
                    if target is None or target == component:
                        continue
                    edges[(component, target)] += 1
                    if target not in allowed:
                        issues.append(
                            {
                                "code": "FORBIDDEN_DEPENDENCY",
                                "component": component,
                                "target": target,
                                "path": relative,
                                "line": node.lineno,
                            }
                        )
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in SYMBOL_OWNERS
                ):
                    if (component, relative) != SYMBOL_OWNERS[node.name]:
                        issues.append(
                            {
                                "code": "DUPLICATED_SEMANTIC_OWNER",
                                "symbol": node.name,
                                "component": component,
                                "path": relative,
                                "line": node.lineno,
                            }
                        )
    return seal(
        {
            "schema_id": "openpine.architecture_report.v1",
            "ok": not issues,
            "policy_hash": digest({"components": COMPONENTS, "symbol_owners": SYMBOL_OWNERS}),
            "inventory": inventory,
            "edges": [
                {"source": a, "target": b, "imports": count}
                for (a, b), count in sorted(edges.items())
            ],
            "issues": issues,
        }
    )
