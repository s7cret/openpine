"""IMPORT-02: allowed consumer/library Pine version combinations."""

from __future__ import annotations

import pytest

from pine2ast.libraries import LibraryError, LibraryStore, link_libraries


def _lib(body: str, version: int, name: str = "Lib") -> str:
    return f'//@version={version}\nlibrary("{name}")\n{body}\n'


def _root(version: int, imports: str = "import user/Lib/1 as lib") -> str:
    header = "indicator" if version >= 5 else "study"
    return f'//@version={version}\n{header}("s2")\n{imports}\nplot(lib.add(1))\n'


@pytest.mark.parametrize("version", [5, 6])
def test_stage2_same_version_consumer_and_library_link(version):
    linked = link_libraries(
        _root(version),
        LibraryStore.create({"user/Lib/1": _lib("export add(int x)=>x+1", version)}),
    )
    linked.verify()


@pytest.mark.parametrize("consumer,library", [(6, 5), (5, 6)])
def test_stage2_mixed_pine_versions_are_rejected(consumer, library):
    with pytest.raises(LibraryError, match="VERSION"):
        link_libraries(
            _root(consumer),
            LibraryStore.create({"user/Lib/1": _lib("export add(int x)=>x+1", library)}),
        )
