"""Expose only test namespaces from restored sources; product imports stay installed."""

from __future__ import annotations

import argparse
from importlib.machinery import ModuleSpec
import os
from pathlib import Path
import sys
from types import ModuleType

from openpine.verification.protected_qualification import installed_origins


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-root", type=Path, required=True)
    parser.add_argument("--stack-root", type=Path, required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    installed_origins()
    root = args.tests_root.resolve()
    # The source parent is never put on sys.path. Original __file__ identities
    # preserve resource and source-mutation fixtures without product overlays.
    for name in ("rc6_tests", "int05_tests", "tests"):
        directory = root / name
        if not directory.is_dir():
            raise ValueError("missing restored test namespace: " + name)
        if name in sys.modules:
            raise ValueError("test namespace was imported before source binding")
        namespace = ModuleType(name)
        namespace.__path__ = [str(directory)]
        namespace.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        namespace.__spec__.submodule_search_locations = namespace.__path__
        sys.modules[name] = namespace
    os.environ["PINE_STACK_ROOT"] = str(args.stack_root.resolve())
    import pytest
    values = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    code = int(pytest.main(values))
    installed_origins()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
