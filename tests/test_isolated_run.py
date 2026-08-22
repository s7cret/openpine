from __future__ import annotations

import sys

import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar
from openpine_contracts import Finality

from openpine.runtime.isolated_run import (
    IsolatedRunError,
    capture_generated_source,
    run_isolated_artifact,
    run_isolated_from_store,
    run_isolated_indicator,
)

SOURCE = (
    "from pinelib.strategy.context import StrategyContext\n"
    "class GeneratedStrategy:\n"
    "    def __init__(self, params=None, runtime=None):\n"
    "        self.ctx = StrategyContext(intent_run_id='run', intent_strategy_id='s')\n"
    "        self.ctx.attach_runtime(runtime)\n"
    "    def _process_bar(self, bar, bar_index):\n"
    "        if bar_index == 2:\n"
    "            self.ctx.entry('L', 'long', qty=1)\n"
)


def _bars() -> list[Bar]:
    return [
        Bar(
            time=1_000 + i,
            open=10.0 + i,
            high=11.0 + i,
            low=9.0 + i,
            close=10.5 + i,
            finality=Finality.FINAL,
        )
        for i in range(6)
    ]


def _cfg(*, semantic_profile: str = "legacy_4x") -> BacktestConfig:
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
    )
    cfg.semantic_profile = semantic_profile
    return cfg


def test_isolated_run_replays_live_tape_without_importing_generated() -> None:
    result = run_isolated_artifact(SOURCE.encode("utf-8"), bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["schema_id"] == "openpine.intent.v2"
    assert result["intent_tape"][0]["kind"] == "entry"
    assert result["score_ledger_hash"]
    assert not any(name.startswith("openpine_generated_") for name in sys.modules)

    class LiveEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 2:
                self.ctx.entry("L", "long", qty=1.0)

    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    assert result["score_ledger_hash"] == live.score_ledger_hash


def test_isolated_run_rejects_artifact_without_tape() -> None:
    source = b"""
from pinelib.strategy.context import StrategyContext
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
        self.ctx.attach_runtime(runtime)
    def _process_bar(self, bar, bar_index):
        return None
"""
    with pytest.raises(IsolatedRunError, match="live pinelib tape"):
        run_isolated_artifact(source, bars=_bars(), config=_cfg())


def test_isolated_run_forwards_trial_params_into_generated_strategy() -> None:
    source = b"""
from pinelib.strategy.context import StrategyContext

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.qty = (params or {})["qty"]
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")

    def _process_bar(self, bar, bar_index=None):
        if bar_index == 2:
            self.ctx.entry("L", "long", qty=self.qty)
"""

    result = run_isolated_artifact(
        source,
        bars=_bars(),
        config=_cfg(),
        params={"qty": 3},
    )

    assert result["intent_tape"][0]["qty"] == "3"


def test_isolated_run_gives_broker_projection_before_each_strategy_decision() -> None:
    source = b"""
from pinelib.strategy.context import StrategyContext
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
        self.ctx.attach_runtime(runtime)
    def _process_bar(self, bar, bar_index):
        if self.ctx.position_size == 0:
            self.ctx.entry("L", "long", qty=1)
        else:
            self.ctx.close("L")
"""

    result = run_isolated_artifact(source, bars=_bars(), config=_cfg(semantic_profile="strict_5x"))

    kinds = [event["kind"] for event in result["intent_tape"]]
    assert kinds[0] == "entry"
    assert "close" in kinds
    assert result["execution_protocol"] == "openpine.worker.protocol.v2"


def test_isolated_worker_exposes_complete_trade_projection_to_generated_code() -> None:
    source = b"""
from pinelib.strategy.context import StrategyContext

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
        self.ctx.attach_runtime(runtime)

    def _process_bar(self, bar, bar_index):
        if self.ctx.position_size == 0 and self.ctx.closedtrades == 0:
            self.ctx.entry("L", "long", qty=1, comment="entry")
        elif self.ctx.opentrades > 0:
            self.ctx.opentrades_entry_price(0)
            self.ctx.opentrades_profit(0)
            self.ctx.opentrades_profit_percent(0)
            self.ctx.opentrades_commission(0)
            self.ctx.opentrades_qty(0)
            self.ctx.opentrades_side(0)
            self.ctx.opentrades_entry_id(0)
            self.ctx.opentrades_exit_price(0)
            self.ctx.opentrades_exit_time(0)
            self.ctx.opentrades_exit_id(0)
            self.ctx.opentrades_size(0)
            self.ctx.opentrades_max_runup(0)
            self.ctx.opentrades_max_drawdown(0)
            self.ctx.opentrades_entry_bar_index(0)
            self.ctx.close("L", comment="exit")
        elif self.ctx.closedtrades > 0:
            self.ctx.closedtrades_entry_price(0)
            self.ctx.closedtrades_exit_price(0)
            self.ctx.closedtrades_entry_time(0)
            self.ctx.closedtrades_exit_time(0)
            self.ctx.closedtrades_profit(0)
            self.ctx.closedtrades_profit_percent(0)
            self.ctx.closedtrades_commission(0)
            self.ctx.closedtrades_qty(0)
            self.ctx.closedtrades_side(0)
            self.ctx.closedtrades_size(0)
            self.ctx.closedtrades_entry_id(0)
            self.ctx.closedtrades_exit_id(0)
            self.ctx.closedtrades_entry_comment(0)
            self.ctx.closedtrades_exit_comment(0)
            self.ctx.closedtrades_max_runup(0)
            self.ctx.closedtrades_max_drawdown(0)
            self.ctx.closedtrades_entry_bar_index(0)
            self.ctx.closedtrades_exit_bar_index(0)
            self.ctx.entry("VERIFIED", "long", qty=1)
"""

    result = run_isolated_artifact(
        source,
        bars=_bars(),
        config=_cfg(semantic_profile="strict_5x"),
    )

    assert any(
        event.get("command_id") == "VERIFIED" for event in result["intent_tape"]
    )


def test_isolated_protocol_supports_same_bar_calc_on_order_fills_recalc() -> None:
    source = b"""
from pinelib.strategy.context import StrategyContext
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.ctx = StrategyContext(
            intent_run_id="run",
            intent_strategy_id="s",
            calc_on_order_fills=True,
            process_orders_on_close=True,
        )
        self.ctx.attach_runtime(runtime)
    def _process_bar(self, bar, bar_index):
        if bar_index == 0 and self.ctx.position_size == 0:
            self.ctx.entry("L", "long", qty=1)
        elif bar_index == 0 and self.ctx.position_size > 0:
            self.ctx.close("L", immediately=True)
"""
    cfg = _cfg(semantic_profile="strict_5x")
    cfg.calc_on_order_fills = True
    cfg.process_orders_on_close = True

    result = run_isolated_artifact(source, bars=_bars(), config=cfg)

    first_bar = [
        event for event in result["intent_tape"] if event["bar_index"] == 0
    ]
    assert [event["kind"] for event in first_bar] == ["entry", "close"]
    assert [event["recalc_iteration"] for event in first_bar] == [0, 1]


def test_isolated_indicator_exposes_htf_close_on_last_confirmed_child_bar() -> None:
    source = b"""
from pinelib.request.security import security

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        value = security(
            "BTCUSDT",
            "1D",
            [10.0, 20.0],
            runtime=self.rt,
            state_id="daily_close",
            gaps="barmerge.gaps_off",
            lookahead="barmerge.lookahead_off",
        )
        self.rt.plot_recorder.record_plot(
            int(bar.time), int(bar_index or 0), value, "daily_close"
        )
"""
    chart_bars = [
        {
            "time": 171_000_000,
            "time_close": 171_899_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 7,
        },
        {
            "time": 171_900_000,
            "time_close": 172_799_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 8,
        },
        {
            "time": 172_800_000,
            "time_close": 173_699_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 9,
        },
    ]
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 10,
            "high": 10,
            "low": 10,
            "close": 10,
            "volume": 1,
        },
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 86_400_000,
            "time_close": 172_799_999,
            "open": 20,
            "high": 20,
            "low": 20,
            "close": 20,
            "volume": 1,
        },
    ]

    result = run_isolated_indicator(
        source,
        chart_bars,
        semantic_profile="strict_5x",
        htf_bars=htf_bars,
    )

    assert [plot[2] for plot in result.plots] == ["na", "10", "20"]


def test_isolated_indicator_derives_chart_timeframe_from_confirmed_bar_clock() -> None:
    source = b"""
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        self.rt.plot_recorder.record_plot(
            int(bar.time), int(bar_index or 0), self.rt.timeframe.interval_ms, "chart_interval"
        )
"""

    result = run_isolated_indicator(
        source,
        [
            {
                "time": 0,
                "time_close": 899_999,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ],
        semantic_profile="strict_5x",
    )

    assert result.plots[0][2] == 900_000


def test_isolated_indicator_preserves_pinelib_gaps_lookahead_matrix() -> None:
    source = b"""
from pinelib.request.security import security

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        modes = (
            ("off_off", "barmerge.gaps_off", "barmerge.lookahead_off"),
            ("on_off", "barmerge.gaps_on", "barmerge.lookahead_off"),
            ("off_on", "barmerge.gaps_off", "barmerge.lookahead_on"),
            ("on_on", "barmerge.gaps_on", "barmerge.lookahead_on"),
        )
        for title, gaps, lookahead in modes:
            value = security(
                "BTCUSDT",
                "1D",
                [10.0, 20.0],
                runtime=self.rt,
                state_id=title,
                gaps=gaps,
                lookahead=lookahead,
            )
            self.rt.plot_recorder.record_plot(
                int(bar.time), int(bar_index or 0), value, title
            )
"""
    chart_bars = [
        {
            "time": time,
            "time_close": time + 899_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        for time in (171_000_000, 171_900_000, 172_800_000, 173_700_000)
    ]
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": time,
            "time_close": time_close,
            "open": value,
            "high": value,
            "low": value,
            "close": value,
            "volume": 1,
        }
        for time, time_close, value in (
            (0, 86_399_999, 10),
            (86_400_000, 172_799_999, 20),
        )
    ]

    result = run_isolated_indicator(
        source,
        chart_bars,
        semantic_profile="strict_5x",
        htf_bars=htf_bars,
    )
    values_by_title: dict[str, list[object]] = {}
    for _time, _index, value, title in result.plots:
        values_by_title.setdefault(title, []).append(value)

    assert values_by_title == {
        "off_off": ["na", "10", "20", "20"],
        "on_off": ["na", "na", "20", "na"],
        "off_on": ["na", "20", "20", "20"],
        "on_on": ["na", "na", "na", "na"],
    }


def test_isolated_indicator_lookahead_off_does_not_repaint_common_prefix() -> None:
    source = b"""
from pinelib.request.security import security

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        value = security(
            "BTCUSDT",
            "1D",
            lambda child: child.close[0],
            runtime=self.rt,
            state_id="daily_close",
            gaps="barmerge.gaps_off",
            lookahead="barmerge.lookahead_off",
        )
        self.rt.plot_recorder.record_plot(
            int(bar.time), int(bar_index or 0), value, "daily_close"
        )
"""
    chart_bars = [
        {
            "time": time,
            "time_close": time + 899_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        for time in (
            171_000_000,
            171_900_000,
            172_800_000,
            258_300_000,
            259_200_000,
        )
    ]
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": time,
            "time_close": time + 86_399_999,
            "open": value,
            "high": value,
            "low": value,
            "close": value,
            "volume": 1,
        }
        for time, value in ((0, 10), (86_400_000, 20), (172_800_000, 30))
    ]

    prefix = run_isolated_indicator(
        source,
        chart_bars[:3],
        semantic_profile="strict_5x",
        htf_bars=htf_bars[:2],
    )
    full = run_isolated_indicator(
        source,
        chart_bars,
        semantic_profile="strict_5x",
        htf_bars=htf_bars,
    )

    prefix_values = [plot[2] for plot in prefix.plots]
    full_prefix_values = [plot[2] for plot in full.plots[: len(prefix.plots)]]
    assert prefix_values == ["na", "10", "20"]
    assert full_prefix_values == prefix_values


def test_isolated_indicator_gaps_on_lookahead_on_emits_at_htf_open() -> None:
    source = b"""
from pinelib.request.security import security

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        value = security(
            "BTCUSDT",
            "1D",
            [10.0, 20.0],
            runtime=self.rt,
            state_id="daily_open",
            gaps="barmerge.gaps_on",
            lookahead="barmerge.lookahead_on",
        )
        self.rt.plot_recorder.record_plot(
            int(bar.time), int(bar_index or 0), value, "daily_open"
        )
"""
    chart_bars = [
        {
            "time": time,
            "time_close": time + 899_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        for time in (86_400_000, 87_300_000)
    ]
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": time,
            "time_close": time + 86_399_999,
            "open": value,
            "high": value,
            "low": value,
            "close": value,
            "volume": 1,
        }
        for time, value in ((0, 10), (86_400_000, 20))
    ]

    result = run_isolated_indicator(
        source,
        chart_bars,
        semantic_profile="strict_5x",
        htf_bars=htf_bars,
    )

    assert [plot[2] for plot in result.plots] == ["na", "20"]


def test_isolated_indicator_rejects_htf_without_confirmed_chart_clock() -> None:
    source = b"""
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        return None
"""

    with pytest.raises(IsolatedRunError, match="confirmed chart bars"):
        run_isolated_indicator(
            source,
            [{"time": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            semantic_profile="strict_5x",
            htf_bars=[
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1D",
                    "time": 0,
                    "time_close": 86_399_999,
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                }
            ],
        )


@pytest.mark.parametrize(
    "chart_bars",
    [
        [
            {
                "time": 0,
                "time_close": 899_999,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
            },
            {"time": 900_000, "open": 1, "high": 1, "low": 1, "close": 1},
        ],
        [
            {
                "time": 1_000,
                "time_close": 999,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
            }
        ],
        [
            {
                "time": 0,
                "time_close": 899_999,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
            },
            {
                "time": 900_000,
                "time_close": 1_199_999,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
            },
        ],
        [
            {
                "time": 0,
                "time_close": 900_000,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
            }
        ],
    ],
    ids=("partial", "nonpositive", "mixed", "exclusive_close"),
)
def test_isolated_indicator_rejects_invalid_confirmed_chart_clock(chart_bars) -> None:
    source = b"""
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        return None
"""
    with pytest.raises(IsolatedRunError, match="chart (bars|timeframe)"):
        run_isolated_indicator(
            source,
            chart_bars,
            semantic_profile="strict_5x",
            htf_bars=[
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1D",
                    "time": 0,
                    "time_close": 86_399_999,
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                }
            ],
        )


@pytest.mark.parametrize("missing", ["time", "open", "high", "low", "close"])
def test_isolated_indicator_rejects_missing_required_chart_field(missing: str) -> None:
    bar = {
        "time": 0,
        "time_close": 59_999,
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
    }
    del bar[missing]

    with pytest.raises(IsolatedRunError, match=f"required field {missing}"):
        run_isolated_indicator(
            b"VALUE = 1\n",
            [bar],
            semantic_profile="strict_5x",
        )


@pytest.mark.parametrize(
    "missing",
    ["symbol", "timeframe", "time", "time_close", "open", "high", "low", "close"],
)
def test_isolated_indicator_rejects_missing_required_htf_field(missing: str) -> None:
    htf_bar = {
        "symbol": "BTCUSDT",
        "timeframe": "1D",
        "time": 0,
        "time_close": 86_399_999,
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "volume": 1,
    }
    del htf_bar[missing]

    with pytest.raises(IsolatedRunError, match=f"HTF bar required field {missing}"):
        run_isolated_indicator(
            b"VALUE = 1\n",
            [
                {
                    "time": 0,
                    "time_close": 59_999,
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                }
            ],
            semantic_profile="strict_5x",
            htf_bars=[htf_bar],
        )


def test_isolated_run_drives_generated_class_to_same_hash() -> None:
    source = (
        "from pinelib.strategy.context import StrategyContext\n"
        "class GeneratedStrategy:\n"
        "    def __init__(self, params=None, runtime=None):\n"
        "        self.rt = runtime\n"
        "        self.ctx = StrategyContext(intent_run_id='run', intent_strategy_id='s')\n"
        "    def _process_bar(self, bar, bar_index=None):\n"
        "        idx = self.rt.bar_index if bar_index is None else bar_index\n"
        "        if idx != 2:\n"
        "            return\n"
        "        self.ctx._runtime = type('RT', (), {"
        "'bar_index': 2, "
        "'current_bar': type('B', (), {'time': getattr(bar, 'time', 1002)})()"
        "})()\n"
        "        self.ctx.entry('L', 'long', qty=1)\n"
    )
    result = run_isolated_artifact(source.encode("utf-8"), bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["bar_index"] == 2

    class LiveEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 2:
                self.ctx.entry("L", "long", qty=1.0)

    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    assert result["score_ledger_hash"] == live.score_ledger_hash


def test_capture_generated_source_uses_bytes_not_later_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    artifact_dir = tmp_path / "art"
    artifact_dir.mkdir()
    path = artifact_dir / "generated_strategy.py"
    path.write_text(SOURCE, encoding="utf-8")

    class Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            return {
                "artifact_dir": str(artifact_dir),
                "python_code": SOURCE,
                "compile_meta": {"compile_status": "OK"},
            }

    import openpine.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ArtifactStore", Store)
    path.write_text("VALUE = 999\n", encoding="utf-8")
    captured = capture_generated_source("src", "art")
    assert captured == SOURCE.encode("utf-8")
    result = run_isolated_artifact(captured, bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["qty"] == "1"


def test_run_isolated_from_store_captures_then_replays(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    artifact_dir = tmp_path / "art"
    artifact_dir.mkdir()
    (artifact_dir / "generated_strategy.py").write_text(SOURCE, encoding="utf-8")

    class Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            return {
                "artifact_dir": str(artifact_dir),
                "python_code": SOURCE,
                "compile_meta": {"compile_status": "OK"},
            }

    import openpine.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ArtifactStore", Store)
    result = run_isolated_from_store("src", "art", bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["kind"] == "entry"
    assert result["score_ledger_hash"]


def test_isolated_indicator_returns_plot_tuples() -> None:
    from openpine.runtime.isolated_run import run_isolated_indicator

    source = (
        "class GeneratedStrategy:\n"
        "    def __init__(self, params=None, runtime=None):\n"
        "        self.rt = runtime\n"
        "    def _process_bar(self, bar, i=0):\n"
        "        self.rt.plot_recorder.record_plot(int(bar.time), int(i), bar.close, 'close')\n"
    )
    result = run_isolated_indicator(
        source.encode("utf-8"),
        _bars()[:2],
        semantic_profile="strict_5x",
    )
    assert result.plots
    assert result.plots[0][3] == "close"
    assert result.plots[0][0] == 1000
    assert isinstance(result.plots[0][2], str)


def _resume_cfg() -> BacktestConfig:
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
        export_resume_state=True,
        resume_validation_policy="diagnostic",
    )
    cfg.semantic_profile = "legacy_4x"
    return cfg


def test_isolated_run_honors_resume_state_without_double_entry() -> None:
    first = run_isolated_artifact(
        SOURCE.encode("utf-8"),
        bars=_bars(),
        config=_resume_cfg(),
    )
    resume = getattr(first["raw_result"], "resume_state", None)
    assert resume is not None
    second = run_isolated_artifact(
        SOURCE.encode("utf-8"),
        bars=_bars(),
        config=_resume_cfg(),
        resume_state=resume,
    )
    assert second["score_ledger_hash"]
    assert getattr(second["raw_result"], "resume_state", None) is not None


def test_isolated_resume_skips_already_replayed_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run

    applied: list[int] = []
    real = isolated_run.apply_live_intents_for_bar

    def _capture(ctx, tape, bar_index, **kwargs):
        applied.append(int(bar_index))
        return real(ctx, tape, bar_index, **kwargs)

    monkeypatch.setattr(isolated_run, "apply_live_intents_for_bar", _capture)
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_010,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_010,
        export_resume_state=True,
        resume_validation_policy="diagnostic",
    )
    cfg.semantic_profile = "legacy_4x"
    source = b"""
from pinelib.strategy.context import StrategyContext
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
        self.ctx.attach_runtime(runtime)
    def _process_bar(self, bar, bar_index):
        if bar_index == 2 and self.ctx.position_size == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 4 and self.ctx.position_size > 0:
            self.ctx.close("L")
"""
    first = run_isolated_artifact(source, bars=_bars()[:3], config=cfg)
    resume = first["raw_result"].resume_state
    assert resume is not None
    warnings = [getattr(item, "code", "") for item in (first["raw_result"].warnings or [])]
    assert "RESUME_STRATEGY_STATE_UNAVAILABLE" not in warnings
    applied.clear()
    run_isolated_artifact(
        source,
        bars=_bars(),
        config=cfg,
        resume_state=resume,
    )
    assert applied
    assert min(applied) > int(resume.bar_index)
    assert 0 not in applied


def test_isolated_run_requires_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}

    class _CaptureSession:
        def __init__(self, source, **kwargs):
            seen["semantic_profile"] = kwargs.get("semantic_profile")
            raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "InteractiveWorkerSession", _CaptureSession)
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        run_isolated_artifact(
            SOURCE.encode("utf-8"),
            bars=_bars(),
            config=_cfg(semantic_profile=""),
        )
    assert "semantic_profile" not in seen


def test_isolated_run_forwards_config_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}

    class _CaptureSession:
        def __init__(self, source, **kwargs):
            seen["semantic_profile"] = kwargs.get("semantic_profile")
            raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "InteractiveWorkerSession", _CaptureSession)
    cfg = _cfg()
    cfg.semantic_profile = "strict_5x"
    with pytest.raises(IsolatedRunError, match="stop"):
        run_isolated_artifact(SOURCE.encode("utf-8"), bars=_bars(), config=cfg)
    assert seen["semantic_profile"] == "strict_5x"


def test_isolated_indicator_forwards_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_run import run_isolated_indicator
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}

    def _capture(source, **kwargs):
        seen["semantic_profile"] = kwargs.get("semantic_profile")
        raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _capture)
    with pytest.raises(IsolatedRunError, match="stop"):
        run_isolated_indicator(b"VALUE = 1\n", _bars(), semantic_profile="strict_5x")
    assert seen["semantic_profile"] == "strict_5x"


def test_isolated_indicator_requires_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_indicator

    seen: dict[str, object] = {}

    def _ok(source, **kwargs):
        seen["semantic_profile"] = kwargs.get("semantic_profile")
        return {"ok": True, "plots": []}

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _ok)
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        run_isolated_indicator(b"VALUE = 1\n", _bars())
    assert "semantic_profile" not in seen


def test_isolated_indicator_rejects_unknown_profile() -> None:
    from openpine.runtime.isolated_run import run_isolated_indicator

    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        run_isolated_indicator(b"VALUE = 1\n", _bars(), semantic_profile="nope")


def test_isolated_run_forwards_confirmed_htf_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    class _CaptureSession:
        def __init__(self, source, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "InteractiveWorkerSession", _CaptureSession)
    with pytest.raises(IsolatedRunError, match="stop"):
        run_isolated_artifact(
            SOURCE.encode("utf-8"),
            bars=_bars(),
            config=_cfg(),
            htf_bars=htf_bars,
        )
    assert seen["htf_bars"] == htf_bars


def test_isolated_run_rejects_unconfirmed_htf_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run

    seen: dict[str, object] = {}

    class _CaptureSession:
        def __init__(self, source, **kwargs):
            seen["called"] = True

    monkeypatch.setattr(isolated_run, "InteractiveWorkerSession", _CaptureSession)
    with pytest.raises(IsolatedRunError, match="confirmed HTF"):
        run_isolated_artifact(
            SOURCE.encode("utf-8"),
            bars=_bars(),
            config=_cfg(),
            htf_bars=[
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1D",
                    "time": 0,
                    "open": 40,
                    "high": 43,
                    "low": 39,
                    "close": 42,
                    "volume": 1,
                }
            ],
        )
    assert "called" not in seen


def test_isolated_indicator_forwards_confirmed_htf_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_run import run_isolated_indicator
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    def _capture(source, **kwargs):
        seen["htf_bars"] = kwargs.get("htf_bars")
        raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _capture)
    with pytest.raises(IsolatedRunError, match="stop"):
        run_isolated_indicator(
            b"VALUE = 1\n",
            _bars(),
            semantic_profile="strict_5x",
            htf_bars=htf_bars,
        )
    assert seen["htf_bars"] == htf_bars


def test_isolated_indicator_rejects_unconfirmed_htf_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_run import run_isolated_indicator

    seen: dict[str, object] = {}

    def _capture(source, **kwargs):
        seen["called"] = True
        return {"ok": True, "plots": []}

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _capture)
    with pytest.raises(IsolatedRunError, match="confirmed HTF"):
        run_isolated_indicator(
            b"VALUE = 1\n",
            _bars(),
            semantic_profile="strict_5x",
            htf_bars=[
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1D",
                    "time": 0,
                    "open": 40,
                    "high": 43,
                    "low": 39,
                    "close": 42,
                    "volume": 1,
                }
            ],
        )
    assert "called" not in seen
