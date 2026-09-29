"""Real source archive and prepared-bundle integrity checks."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from openpine.verification import execution_ci
from openpine.verification.execution_identity import source_snapshot
from openpine.verification.identity import seal


def _roots(tmp_path: Path) -> dict[str, Path]:
    roots = {}
    for name in execution_ci.COMPONENTS:
        root = tmp_path / "sources" / name
        root.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "' + name + '"\nversion = "5.0.0rc6"\n', encoding="utf-8"
        )
        roots[name] = root
    script = roots["openpine"] / "scripts/check.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return roots


def _archive_with_entries(path: Path, entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as stream:
        for info, payload in entries:
            stream.addfile(info, io.BytesIO(payload) if info.isfile() else None)


def test_exact_source_archive_restores_bytes_modes_and_every_component(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    archive = tmp_path / "sources.tar.gz"
    expected = execution_ci.create_source_archive(roots, archive)
    assert expected == source_snapshot(roots)
    assert set(expected["components"]) == set(execution_ci.COMPONENTS)
    restored = execution_ci.unpack_source_archive(archive, tmp_path / "restored", expected)
    assert source_snapshot(restored) == expected
    assert (restored["openpine"] / "scripts/check.sh").read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert (restored["openpine"] / "scripts/check.sh").stat().st_mode & 0o111
    assert not ((restored["openpine"] / "pyproject.toml").stat().st_mode & 0o111)


@pytest.mark.parametrize("member_name", ["openpine/../escape", "/absolute/escape", "openpine\\other", "other/unknown"])
def test_source_archive_rejects_unsafe_members(tmp_path: Path, member_name: str) -> None:
    roots = _roots(tmp_path)
    manifest = source_snapshot(roots)
    info = tarfile.TarInfo(member_name)
    info.size = 1
    path = tmp_path / "untrusted.tar.gz"
    _archive_with_entries(path, [(info, b"x")])
    with pytest.raises(ValueError, match="unsafe or unexpected source archive member"):
        execution_ci.unpack_source_archive(path, tmp_path / "unpacked", manifest)
    assert not (tmp_path / "escape").exists()


def test_source_archive_rejects_truncated_and_duplicate_inventory(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    manifest = source_snapshot(roots)
    path = tmp_path / "broken.tar.gz"
    entries = []
    for component, root in roots.items():
        content = (root / "pyproject.toml").read_bytes()
        item = tarfile.TarInfo(component + "/pyproject.toml")
        item.size = len(content)
        entries.append((item, content))
    info, body = entries[0]
    _archive_with_entries(path, entries)
    with pytest.raises(ValueError, match="omitted required files"):
        execution_ci.unpack_source_archive(path, tmp_path / "missing", manifest)
    _archive_with_entries(path, [*entries, (info, body)])
    with pytest.raises(ValueError, match="unsafe or unexpected source archive member"):
        execution_ci.unpack_source_archive(path, tmp_path / "duplicate", manifest)
    info.size -= 1
    _archive_with_entries(path, [(info, body[:-1])])
    with pytest.raises(ValueError, match="differs from exact input manifest"):
        execution_ci.unpack_source_archive(path, tmp_path / "wrong_size", manifest)


def test_exact_archive_rejects_changed_content_and_symlink_in_bundle(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    manifest = source_snapshot(roots)
    archive = tmp_path / "corrupt.tar.gz"
    payload = b"x" * len((roots["openpine"] / "pyproject.toml").read_bytes())
    info = tarfile.TarInfo("openpine/pyproject.toml")
    info.size = len(payload)
    _archive_with_entries(archive, [(info, payload)])
    with pytest.raises(ValueError, match="checksum mismatch"):
        execution_ci.unpack_source_archive(archive, tmp_path / "wrong_hash", manifest)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "payload").write_bytes(b"exact")
    environment = seal({"schema_id": "openpine.execution_environment.v1", "python": "3.13"})
    report = execution_ci.bundle_manifest(bundle, source=manifest, environment=environment,
        source_pins={"marketdata-provider": "a" * 40}, host_commit="b" * 40)
    assert execution_ci.verify_bundle(bundle)["content_hash"] == report["content_hash"]
    (bundle / "payload").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="artifact checksum mismatch"):
        execution_ci.verify_bundle(bundle)
    (bundle / "payload").write_bytes(b"exact")
    (bundle / "link").symlink_to("payload")
    with pytest.raises(ValueError, match="bundle contains a symlink"):
        execution_ci.verify_bundle(bundle)
