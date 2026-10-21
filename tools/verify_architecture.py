"""Enforce package directions, including imports nested inside functions."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "contracts": {"contracts"},
    "compiler": {"contracts", "compiler"},
    "runtime": {"contracts", "runtime"},
    "marketdata": {"contracts", "marketdata"},
    "storage": {"contracts", "marketdata", "storage"},
    "execution": {"contracts", "compiler", "runtime", "marketdata", "execution", "simulation"},
    "simulation": {"contracts", "simulation"},
}


def violations(root=ROOT):
    errors = []
    for path in sorted((root / "packages").rglob("*.py")):
        relative = path.relative_to(root)
        layer = relative.parts[1] if len(relative.parts) > 2 else None
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node,ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node,ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = list(relative.with_suffix("").parts[:-1])
                    base = ".".join(package[:len(package)-node.level+1] + ([base] if base else []))
                names = [base + "." + alias.name for alias in node.names]
            for name in names:
                parts = name.split(".")
                if (parts[0] in ("apps","tools")
                        or parts[0] in ("apsw","duckdb","sqlite3") and layer != "storage"
                        or parts[0] == "packages" and len(parts)>1 and parts[1] not in ALLOWED.get(layer,set())):
                    errors.append(f"{relative.as_posix()}:{node.lineno}: forbidden dependency {name}")
    return errors


def main():
    errors = violations()
    for error in errors:
        print(error)
    if not errors:
        print("R6 architecture boundaries passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
