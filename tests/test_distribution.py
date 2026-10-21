import base64
import csv
import hashlib
import io
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

from packages.compiler.semantic.specification import ROOT, load_snapshot
from packages.execution.session import implementation_hash
from tools.build_backend import build_wheel


class DistributionTests(unittest.TestCase):
    def test_reproducible_wheel_preserves_all_pinned_assets_and_record(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            first = directory / build_wheel(directory)
            raw = first.read_bytes()
            build_wheel(directory)
            self.assertEqual(first.read_bytes(), raw)
            with zipfile.ZipFile(first) as wheel:
                names = set(wheel.namelist())
                self.assertIn("apps/ui/static/app.js", names)
                self.assertIn("apps/worker/__main__.py", names)
                self.assertFalse(any("__pycache__" in name or ".runtime" in name or ".git/" in name for name in names))
                for path in (ROOT / "spec").rglob("*"):
                    if path.is_file() and path.name != ".gitkeep":
                        self.assertEqual(wheel.read(path.relative_to(ROOT).as_posix()), path.read_bytes())
                record_name = next(name for name in names if name.endswith(".dist-info/RECORD"))
                rows = list(csv.reader(io.StringIO(wheel.read(record_name).decode())))
                self.assertEqual({row[0] for row in rows}, names)
                for name, sha, size in rows:
                    if name == record_name:
                        continue
                    raw = wheel.read(name)
                    self.assertEqual(sha, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode())
                    self.assertEqual(int(size), len(raw))
                site = directory / "installed"
                wheel.extractall(site)
            load_snapshot(site)
            for package in ("apps", "packages"):
                expected = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (ROOT / package).rglob("*.py")}
                actual = {p.relative_to(site).as_posix(): p.read_bytes() for p in (site / package).rglob("*.py")}
                self.assertEqual(actual, expected)

    def test_distribution_has_single_product_and_python_314_dependencies(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["requires-python"], ">=3.14,<3.15")
        self.assertEqual(project["scripts"]["openpine"], "apps.openpine.cli:main")
        self.assertFalse(any("pine" in dependency for dependency in project["dependencies"]))
