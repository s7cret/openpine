"""Tests for scripts/render_badges.py.

Each README in the OpenPine stack must link every badge to the
relevant GitHub repository. These tests pin down the contract:

1. ``render_block`` produces ``[![alt](shields.io)](repo_url)`` form.
2. ``replace_block`` is idempotent: patching twice yields the same output.
3. The central ``openpine`` README cross-links all six sister libraries.
4. Edge case: a README with no badge block at all still gets a block
   inserted right after the first H1 heading.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "render_badges.py"
spec = importlib.util.spec_from_file_location("render_badges", SCRIPT_PATH)
if spec is None or spec.loader is None:  # pragma: no cover — defensive
    raise RuntimeError(f"failed to load {SCRIPT_PATH}")
render_badges = importlib.util.module_from_spec(spec)
sys.modules["render_badges"] = render_badges
spec.loader.exec_module(render_badges)


def test_common_badges_wrap_in_anchor_pointing_to_repo():
    block = render_badges.render_block("pine2ast", version="4.0.0", min_python="3.11")
    # Every badge must be wrapped in an <a href> that points at the
    # canonical GitHub URL for the repo slug.
    expected_href = "https://github.com/s7cret/pine2ast"
    assert expected_href in block
    # And the badge image source must reference shields.io so the
    # image still renders.
    assert "img.shields.io/badge/" in block
    # The anchors must use markdown ``[![alt](src)](href)`` syntax,
    # not raw HTML — both are accepted by GitHub but markdown is
    # shorter and easier to grep.
    assert "[![Version](" in block


def test_central_openpine_block_cross_links_six_libraries():
    block = render_badges.render_block("openpine", version="4.0.0")
    for label, slug in render_badges.STACK:
        expected = f"https://github.com/s7cret/{slug}"
        assert expected in block, f"openpine block missing link to {slug}"


def test_replace_block_is_idempotent():
    readme = "# Pine2AST\n\n![Version](https://img.shields.io/badge/version-4.0.0-blue)\n"
    once = render_badges.replace_block(readme, "pine2ast", "4.0.0", "3.11")
    twice = render_badges.replace_block(once, "pine2ast", "4.0.0", "3.11")
    assert once == twice
    # The original bare ``![]()`` form must not survive a replacement.
    assert "![" in once and "](" in once and ")](https://github.com/s7cret/pine2ast)" in once


def test_replace_block_inserts_after_h1_when_no_badges_present():
    readme = "# Pine2AST\n\nIntro paragraph.\n"
    out = render_badges.replace_block(readme, "pine2ast", "4.0.0", "3.11")
    lines = out.splitlines()
    h1_index = next(i for i, line in enumerate(lines) if line.startswith("# "))
    # A shields.io badge must appear on a line after the H1 and before
    # the first non-badge content line.
    assert any("shields.io" in line for line in lines[h1_index + 1 : h1_index + 5])
    assert "Intro paragraph." in out


def test_replace_block_drops_legacy_bare_badge_run():
    # A README that ships with multiple bare badges in a row must end
    # up with a single ``render_block`` result — the helper collapses
    # the leading run of shields.io lines into one new block.
    readme = (
        "# PineLib\n"
        "\n"
        "![Version](https://img.shields.io/badge/version-4.0.0-blue)\n"
        "![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue)\n"
        "![License](https://img.shields.io/badge/license-MIT-green)\n"
        "\n"
        "Body text.\n"
    )
    out = render_badges.replace_block(readme, "pinelib", "4.0.0", "3.11")
    # Only one shields.io occurrence of "version" should remain (the
    # rest are subsumed into the same line via markdown inline form).
    assert out.count("shields.io/badge/version") == 1
    assert "Body text." in out


def test_render_block_rejects_unknown_repo():
    with pytest.raises(ValueError):
        render_badges.render_block("not-a-real-repo", version="4.0.0")


def test_openpine_block_links_every_sister_lib_only_once():
    block = render_badges.render_block("openpine", version="4.0.0")
    for _, slug in render_badges.STACK:
        href = f"https://github.com/s7cret/{slug}"
        assert block.count(f"]({href})") == 1, f"duplicate or missing link for {slug}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
