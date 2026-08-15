import os
import sys
from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import (
    IsolatedGeneratedAdapter,
    IsolatedWorkerError,
    admit_generated_source,
    generated_module_imported_in_parent,
    make_isolated_adapter,
    run_isolated_generated,
)


def test_socket_import_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text("import socket\n")
    with pytest.raises(IsolatedWorkerError, match="socket"):
        admit_generated_source(path)


def test_side_effect_stays_in_worker(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text(
        "class GeneratedStrategy:\n"
        "    def __init__(self, params, runtime, ctx):\n"
        "        self.ctx = ctx\n"
        "    def _process_bar(self, bar, bar_index):\n"
        "        self.ctx.entry('L', 'long', qty=1)\n"
    )
    os.environ.pop("OPENPINE_SIDE_EFFECT", None)
    payload = run_isolated_generated(
        path,
        {
            "action": "process_bar",
            "bar_index": 0,
            "bar": {"time": 1, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "0"},
        },
    )
    assert payload["ok"] is True
    assert payload["intents"][0]["kind"] == "entry"
    assert "OPENPINE_SIDE_EFFECT" not in os.environ
    assert generated_module_imported_in_parent() is False
    assert "openpine_worker_generated" not in sys.modules


def test_parent_adapter_never_imports_generated_module(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text(
        "class GeneratedStrategy:\n"
        "    def __init__(self, params, runtime, ctx):\n"
        "        self.ctx = ctx\n"
        "    def _process_bar(self, bar, bar_index):\n"
        "        self.ctx.entry('L', 'long', qty='1')\n"
    )
    adapter_cls = make_isolated_adapter(path)
    assert adapter_cls.__name__ == "GeneratedStrategy"
    assert issubclass(adapter_cls, IsolatedGeneratedAdapter)

    class _Bar:
        time = 1
        open = 1
        high = 1
        low = 1
        close = 1
        volume = 0

    class _Ctx:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def _record_intent(self, kind: str, order_id: str, **kwargs: object) -> None:
            self.calls.append((kind, order_id))

        @property
        def intent_tape(self) -> object:
            return self

    adapter = adapter_cls({}, None, _Ctx())
    adapter._process_bar(_Bar(), 0)
    assert generated_module_imported_in_parent() is False
    assert "openpine_worker_generated" not in sys.modules
