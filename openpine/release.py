"""OpenPine release readiness checks."""

from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from openpine import __version__
from openpine.distribution import distribution_manifest
from openpine.quality import architecture_report, duplicate_report
from openpine.storage.migrations import _get_migration_files
from openpine.storage.schema_indexes import REQUIRED_INDEXES

REQUIRED_STACK_VERSION = "4.0.0"
CANONICAL_DOCS = {
    "docs/README.md",
    "docs/ARCHITECTURE.md",
    "docs/DATABASE.md",
    "docs/DEVELOPMENT.md",
    "docs/RELEASE_4_0.md",
    "docs/WEB_UI_BOUNDARY.md",
}


@dataclass(frozen=True)
class ReleaseReport:
    ok: bool
    version: str
    errors: tuple[str, ...]
    checks: dict[str, object]


def _pyproject(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _dependency_errors(project: dict) -> list[str]:
    errors: list[str] = []
    deps = "\n".join(project.get("dependencies", []))
    expected_fragments = {
        "pine2ast": f"pine2ast.git@v{REQUIRED_STACK_VERSION}",
        "ast2python": f"ast2python.git@v{REQUIRED_STACK_VERSION}",
        "pinelib": f"pinelib.git@v{REQUIRED_STACK_VERSION}",
        "backtest-engine": f"backtest_engine.git@v{REQUIRED_STACK_VERSION}",
        "marketdata-provider": f"marketdata-provider.git@v{REQUIRED_STACK_VERSION}",
        "optimizer": f"optimizer.git@v{REQUIRED_STACK_VERSION}",
    }
    for package, fragment in expected_fragments.items():
        if fragment not in deps:
            errors.append(f"dependency tag for {package} is not v{REQUIRED_STACK_VERSION}")
    return errors


def release_report(root: Path) -> ReleaseReport:
    project = _pyproject(root)["project"]
    errors: list[str] = []
    if project["version"] != __version__:
        errors.append(f"pyproject version {project['version']} != package version {__version__}")
    if __version__ != REQUIRED_STACK_VERSION:
        errors.append(f"package version {__version__} != required {REQUIRED_STACK_VERSION}")
    errors.extend(_dependency_errors(project))
    missing_docs = sorted(path for path in CANONICAL_DOCS if not (root / path).is_file())
    if missing_docs:
        errors.append(f"missing canonical docs: {missing_docs}")
    migrations = _get_migration_files(root / "openpine" / "storage" / "migrations")
    if not migrations or migrations[-1][0] < 11:
        errors.append("expected performance index migration 011 or later")
    migration_sql = "\n".join(path.read_text(encoding="utf-8") for _, _, path in migrations)
    missing_index_sql = [index.name for index in REQUIRED_INDEXES if index.name not in migration_sql]
    if missing_index_sql:
        errors.append(f"required SQLite indexes missing from migrations: {missing_index_sql}")
    arch = architecture_report(root / "openpine", max_lines=4000)
    dup = duplicate_report(root / "openpine")
    dist = distribution_manifest(root)
    if arch.oversized_count:
        errors.append(f"architecture oversized files: {arch.oversized}")
    if dup.duplicate_group_count:
        errors.append(f"duplicate function groups: {dup.duplicate_groups}")
    if dist.hygiene_errors:
        errors.append(f"distribution hygiene errors: {dist.hygiene_errors}")
    checks = {
        "architecture": asdict(arch),
        "duplicates": asdict(dup),
        "distribution": asdict(dist),
        "migration_count": len(migrations),
        "latest_migration": migrations[-1][0] if migrations else None,
        "required_index_count": len(REQUIRED_INDEXES),
    }
    return ReleaseReport(ok=not errors, version=__version__, errors=tuple(errors), checks=checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openpine.release")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args(argv)
    report = release_report(Path(args.root).resolve())
    payload = asdict(report)
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.json_path:
        Path(args.json_path).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
