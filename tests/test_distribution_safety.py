from __future__ import annotations

import os
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

import openpine.distribution as distribution_module
from openpine.distribution import (
    DistributionSizeError,
    DistributionSourceError,
    build_zip,
    distribution_manifest,
    source_files,
)


def _write(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _init_git_index(root: Path, tracked_paths: list[str]) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "--", *tracked_paths], check=True)


def _relative_names(root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in source_files(root)]


def test_git_checkout_selects_only_tracked_non_research_payloads(tmp_path: Path) -> None:
    tracked_paths = [
        "keep.py",
        "research/study/CONTRACT.md",
        "research/study/scripts/analyze.py",
        "research/study/data/tracked.csv",
        "research/study/artifacts/tracked.json",
    ]
    _write(tmp_path, "keep.py", "print('safe')\n")
    _write(tmp_path, "research/study/CONTRACT.md", "# Contract\n")
    _write(tmp_path, "research/study/scripts/analyze.py", "print('research')\n")
    _write(tmp_path, "research/study/data/tracked.csv", "secret,tracked\n")
    _write(tmp_path, "research/study/artifacts/tracked.json", "{}\n")
    _init_git_index(tmp_path, tracked_paths)

    _write(tmp_path, "untracked-notes.txt", "must not ship\n")
    _write(tmp_path, "research/study/data/untracked.csv", "secret,untracked\n")
    _write(tmp_path, "research/study/artifacts/untracked.json", "{}\n")
    _write(tmp_path, "research/study/results/untracked.json", "{}\n")
    _write(tmp_path, "research/study/cache/untracked.bin", "cache\n")
    _write(tmp_path, "research/study/generated-release.zip", "not a real zip\n")

    expected = [
        "keep.py",
        "research/study/CONTRACT.md",
        "research/study/scripts/analyze.py",
    ]
    manifest = distribution_manifest(tmp_path)
    output = tmp_path.parent / "tracked-source.zip"
    build_zip(tmp_path, output, archive_root="openpine-test")

    assert _relative_names(tmp_path) == expected
    assert manifest.file_count == len(expected)
    assert manifest.byte_count == sum((tmp_path / name).stat().st_size for name in expected)
    assert manifest.hygiene_errors == ()
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [f"openpine-test/{name}" for name in expected]


def test_nested_root_inside_parent_git_checkout_fails_closed(tmp_path: Path) -> None:
    nested_root = tmp_path / "nested"
    _write(nested_root, "keep.py", "print('tracked')\n")
    _init_git_index(tmp_path, ["nested/keep.py"])
    _write(nested_root, "untracked-secret.txt", "must not ship\n")

    with pytest.raises(DistributionSourceError, match=r"nested.*Git checkout"):
        source_files(nested_root)


def test_git_checkout_rejects_tracked_file_beneath_parent_symlink(
    tmp_path: Path,
) -> None:
    tracked_file = _write(tmp_path, "payload/keep.txt", "original\n")
    _init_git_index(tmp_path, ["payload/keep.txt"])
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside"
    _write(outside_root, "keep.txt", "outside secret\n")
    tracked_file.unlink()
    tracked_file.parent.rmdir()
    tracked_file.parent.symlink_to(outside_root, target_is_directory=True)
    output = tmp_path.parent / f"{tmp_path.name}-symlink.zip"

    with pytest.raises(DistributionSourceError, match=r"symlinked component.*payload"):
        build_zip(tmp_path, output, archive_root="openpine-test")

    assert not output.exists()


def test_non_git_fallback_is_sorted_and_excludes_generated_payloads(tmp_path: Path) -> None:
    _write(tmp_path, "z-last.py", "print('z')\n")
    _write(tmp_path, "a-first.py", "print('a')\n")
    _write(tmp_path, "research/study/CONTRACT.md", "# Contract\n")
    _write(tmp_path, "research/study/scripts/analyze.py", "print('research')\n")
    _write(tmp_path, "research/study/data/raw.csv", "private\n")
    _write(tmp_path, "research/study/artifacts/report.json", "{}\n")
    _write(tmp_path, "research/study/results/result.json", "{}\n")
    _write(tmp_path, "research/study/cache/cache.bin", "cache\n")
    _write(tmp_path, "build/package.whl", "wheel\n")
    _write(tmp_path, ".pytest_cache/state", "cache\n")
    _write(tmp_path, ".git/objects/object", "vcs metadata\n")
    _write(tmp_path, "generated.zip", "not a real zip\n")

    assert _relative_names(tmp_path) == [
        "a-first.py",
        "research/study/CONTRACT.md",
        "research/study/scripts/analyze.py",
        "z-last.py",
    ]


def test_manifest_rejects_a_file_over_the_per_file_cap(tmp_path: Path) -> None:
    _write(tmp_path, "big.bin", "1234")

    with pytest.raises(
        ValueError,
        match=r"per-file size cap.*big\.bin.*4 bytes.*3 bytes",
    ):
        distribution_manifest(tmp_path, max_file_bytes=3, max_total_bytes=10)


def test_zip_rejects_sources_over_the_total_cap_before_creating_output(tmp_path: Path) -> None:
    _write(tmp_path, "a.bin", "1234")
    _write(tmp_path, "b.bin", "5678")
    output = tmp_path / "release.zip"

    with pytest.raises(
        ValueError,
        match=r"total size cap.*8 bytes.*7 bytes",
    ):
        build_zip(
            tmp_path,
            output,
            archive_root="openpine-test",
            max_file_bytes=8,
            max_total_bytes=7,
        )

    assert not output.exists()


def test_build_zip_streams_files_without_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path, "keep.txt", "stream me\n")
    output = tmp_path / "release.zip"

    def forbidden_read_bytes(_path: Path) -> bytes:
        raise AssertionError("distribution ZIP creation must not use Path.read_bytes()")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    digest = build_zip(tmp_path, output, archive_root="openpine-test")

    assert len(digest) == 64
    assert source in source_files(tmp_path)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("openpine-test/keep.txt") == b"stream me\n"


def test_build_zip_normalizes_metadata_for_reproducible_archives(tmp_path: Path) -> None:
    source = _write(tmp_path, "keep.txt", "deterministic\n")
    first_output = tmp_path.parent / "first-source.zip"
    second_output = tmp_path.parent / "second-source.zip"

    first_digest = build_zip(tmp_path, first_output, archive_root="openpine-test")
    os.utime(source, (2_000_000_000, 2_000_000_000))
    second_digest = build_zip(tmp_path, second_output, archive_root="openpine-test")

    assert first_digest == second_digest
    assert first_output.read_bytes() == second_output.read_bytes()
    with zipfile.ZipFile(first_output) as archive:
        info = archive.getinfo("openpine-test/keep.txt")
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.create_system == 3
        assert info.external_attr == 0o644 << 16


def test_build_zip_enforces_caps_again_while_streaming_growing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path, "keep.txt", "tiny")
    output = tmp_path.parent / "growing-source.zip"
    original_source_files = distribution_module.source_files

    def grow_after_validation(root: Path, **kwargs) -> list[Path]:
        selected = original_source_files(root, **kwargs)
        source.write_bytes(b"x" * 32)
        return selected

    monkeypatch.setattr(distribution_module, "source_files", grow_after_validation)

    with pytest.raises(DistributionSizeError, match=r"per-file size cap.*keep.txt"):
        build_zip(
            tmp_path,
            output,
            archive_root="openpine-test",
            max_file_bytes=16,
            max_total_bytes=64,
        )

    assert not output.exists()


def test_build_zip_rejects_parent_symlink_swap_after_source_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path, "payload/keep.txt", "original\n")
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside-swap"
    _write(outside_root, "keep.txt", "outside secret\n")
    output = tmp_path.parent / "parent-swap.zip"
    original_source_files = distribution_module.source_files

    def swap_parent_after_validation(root: Path, **kwargs) -> list[Path]:
        selected = original_source_files(root, **kwargs)
        source.parent.rename(tmp_path / "original-payload")
        source.parent.symlink_to(outside_root, target_is_directory=True)
        return selected

    monkeypatch.setattr(distribution_module, "source_files", swap_parent_after_validation)

    with pytest.raises(DistributionSourceError, match=r"open source file safely.*payload/keep.txt"):
        build_zip(tmp_path, output, archive_root="openpine-test")

    assert not output.exists()


def test_build_zip_rejects_non_regular_source_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)
    output = tmp_path.parent / "fifo-source.zip"
    monkeypatch.setattr(distribution_module, "source_files", lambda *_args, **_kwargs: [fifo])

    with pytest.raises(DistributionSourceError, match=r"not regular.*source.fifo"):
        build_zip(tmp_path, output, archive_root="openpine-test")

    assert not output.exists()


def test_gitignore_ignores_only_generated_research_payloads() -> None:
    root = Path(__file__).resolve().parents[1]
    ignored_paths = [
        "research/example/data/raw.csv",
        "research/example/artifacts/report.json",
        "research/example/results/result.json",
        "research/example/cache/cache.json",
        "research/example/caches/cache.json",
        "research/example/generated.zip",
    ]
    trackable_paths = [
        "research/example/CONTRACT.md",
        "research/example/scripts/analyze.py",
    ]

    for relative_path in ignored_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", relative_path],
            cwd=root,
            check=False,
        )
        assert result.returncode == 0, f"expected generated path to be ignored: {relative_path}"

    for relative_path in trackable_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", relative_path],
            cwd=root,
            check=False,
        )
        assert result.returncode == 1, f"expected source/contract to remain trackable: {relative_path}"


def test_stack_ci_pins_every_sibling_checkout_to_an_immutable_sha() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stack-ci.yml").read_text(
        encoding="utf-8"
    )

    refs = re.findall(r"^\s+ref:\s+([^\s#]+)", workflow, flags=re.MULTILINE)
    assert len(refs) == 6
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)


def test_backend_ci_covers_every_supported_python_minor() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python-version: ${{ matrix.python-version }}" in workflow
    for version in ("'3.11'", "'3.12'", "'3.13'"):
        assert version in workflow


def test_runtime_imports_are_declared_as_package_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = config["project"]["dependencies"]

    assert any(
        re.split(r"[<>=!~\[]", str(item), maxsplit=1)[0] == "msgpack"
        for item in dependencies
    )


def test_stack_release_reports_run_inside_each_checkout() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stack-ci.yml").read_text(
        encoding="utf-8"
    )

    modules = {
        "openpine": "openpine.release",
        "pine2ast": "pine2ast.release",
        "ast2python": "ast2python.release",
        "pinelib": "pinelib.release",
        "backtest_engine": "backtest_engine.release",
        "marketdata-provider": "marketdata_provider.release",
        "optimizer": "optimizer.release",
    }
    for checkout, module in modules.items():
        assert f"(cd {checkout} && python -m {module} --root .)" in workflow
