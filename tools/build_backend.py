"""Small standard-library PEP 517 wheel backend for the single R6 repository.

The wheel retains the repository's shared asset root (spec/examples/conformance)
beside its Python packages, so offline verification and execution use identical
bytes before and after installation. No generated source or legacy dependencies.
"""

import argparse
import base64
import csv
import hashlib
import io
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def metadata_files():
    p = project()
    dist = f"{p['name']}-{p['version']}.dist-info"
    metadata = (f"Metadata-Version: 2.4\nName: {p['name']}\nVersion: {p['version']}\n"
                f"Summary: {p['description']}\nRequires-Python: {p['requires-python']}\n"
                "License-Expression: MIT\nLicense-File: LICENSE\n" +
                "".join(f"Requires-Dist: {d}\n" for d in p["dependencies"]) + "\n")
    return dist, {f"{dist}/METADATA": metadata.encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: openpine-r6\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist}/entry_points.txt": b"[console_scripts]\nopenpine = apps.openpine.cli:main\n",
        f"{dist}/licenses/LICENSE": (ROOT / "LICENSE").read_bytes()}


def get_requires_for_build_wheel(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    dist, files = metadata_files()
    for name, raw in files.items():
        path = Path(metadata_directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return dist


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    p = project()
    files = {}
    directories = ("packages", "apps", "tools", "spec", "examples", "conformance", "docs", "tests")
    suffixes = {".py", ".json", ".ndjson", ".pine", ".md", ".html", ".css", ".js", ".txt"}
    for directory in directories:
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and (directory == "spec" or path.suffix in suffixes) and "__pycache__" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ("README.md", "LICENSE", "pyproject.toml", "requirements-dev.txt", ".python-version"):
        files[name] = (ROOT / name).read_bytes()
    dist, metadata = metadata_files()
    files.update(metadata)
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, raw in sorted(files.items()):
        sha = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + sha, len(raw)))
    writer.writerow((f"{dist}/RECORD", "", ""))
    files[f"{dist}/RECORD"] = record.getvalue().encode()
    name = f"{p['name']}-{p['version']}-py3-none-any.whl"
    output = Path(wheel_directory)
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output / name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for path, raw in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(2026, 10, 21, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            wheel.writestr(info, raw)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist")
    args = parser.parse_args(argv)
    print(Path(args.output) / build_wheel(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
