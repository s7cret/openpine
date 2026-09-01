from __future__ import annotations

from backtest_engine.core.delegated_strategy_intents import (
    DelegatedStrategyIntentHandler,
    build_delegated_strategy_dispatcher,
)
from backtest_engine.core.intent_replay import IntentReplayIdentity
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession
from openpine.runtime.rc6_executor import RC6RuntimeExecutor
from openpine_contracts import validate_payload, verify_content_hash
from pinelib import RuntimeLanguageContext
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext


SOURCE = '''//@version=6
strategy("rc6 delegated runtime")
if close > open
    strategy.entry("L", strategy.long, qty=1)
'''
COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}
BAR_TIME = 1_725_145_600_000


def test_compiled_strategy_commits_backtest_owned_intent() -> None:
    compiled = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="generated_rc6_delegated_runtime",
        source_name="rc6-delegated.pine",
        producer_commits=COMMITS,
    )
    assert compiled.success, compiled.errors
    handler = DelegatedStrategyIntentHandler(
        identity=IntentReplayIdentity(
            run_id="run-rc6",
            strategy_id="strategy-rc6",
            stack_id="sha256:" + "c" * 64,
            semantic_profile="strict_5x",
            series_id="series-rc6",
            instrument_id="BINANCE:SOLUSDT",
            timeframe="1m",
        ),
        producer_commit="d" * 40,
        bar_open_time_utc_ms={0: BAR_TIME},
    )
    executor = RC6RuntimeExecutor(
        artifact={
            "generated_artifact": compiled.generated_artifact,
            "python_code": compiled.python_code,
            "consumer_bundle": compiled.consumer_bundle,
            "source_map": compiled.source_map,
            "compile_meta": compiled.compile_meta,
        },
        language=RuntimeLanguageContext(
            6,
            "2026-09-01",
            "pine-v6",
            "sha256:" + "e" * 64,
            "compiler_annotation",
        ),
        instrument=InstrumentContext(
            ticker="SOLUSDT",
            tickerid="BINANCE:SOLUSDT",
            prefix="BINANCE",
            currency="USDT",
            basecurrency="SOL",
            timezone="UTC",
            instrument_type="crypto",
            mintick=0.01,
        ),
        timeframe=TimeframeContext.parse("1"),
        delegated_dispatcher=build_delegated_strategy_dispatcher(handler),
    )

    committed = executor.execute_bar(
        BarValues(
            open=100,
            high=102,
            low=99,
            close=101,
            volume=1000,
            time=BAR_TIME,
            time_close=BAR_TIME + 60_000,
        ),
        bar_index=0,
        last_bar_index=0,
    )
    intents = handler.seal_committed(
        [output.value for output in committed.delegated_outputs]
    )

    assert len(intents) == 1
    assert intents[0]["kind"] == "entry"
    assert intents[0]["direction"] == "LONG"
    assert intents[0]["qty"] == "1"
    validate_payload("openpine.intent.v2", intents[0])
    assert verify_content_hash(intents[0], schema_id="openpine.intent.v2")


def test_generated_strategy_session_reads_current_broker_projection() -> None:
    source = '''//@version=6
strategy("rc6 projected runtime")
if strategy.position_size == 0
    strategy.entry("L", strategy.long, qty=1)
'''
    compiled = NativeRC6CompilerAdapter().compile(
        source,
        module_name="generated_rc6_projected_runtime",
        source_name="rc6-projected.pine",
        producer_commits=COMMITS,
    )
    assert compiled.success, compiled.errors
    session = RC6GeneratedScriptSession(
        artifact={
            "generated_artifact": compiled.generated_artifact,
            "python_code": compiled.python_code,
            "consumer_bundle": compiled.consumer_bundle,
            "source_map": compiled.source_map,
            "compile_meta": compiled.compile_meta,
        },
        language=RuntimeLanguageContext(
            6,
            "2026-09-01",
            "pine-v6",
            "sha256:" + "e" * 64,
            "compiler_annotation",
        ),
        instrument=InstrumentContext(
            ticker="SOLUSDT",
            tickerid="BINANCE:SOLUSDT",
            prefix="BINANCE",
            currency="USDT",
            basecurrency="SOL",
            timezone="UTC",
            instrument_type="crypto",
            mintick=0.01,
        ),
        timeframe=TimeframeContext.parse("1"),
        identity=IntentReplayIdentity(
            run_id="run-rc6-projection",
            strategy_id="strategy-rc6-projection",
            stack_id="sha256:" + "c" * 64,
            semantic_profile="strict_5x",
            series_id="series-rc6-projection",
            instrument_id="BINANCE:SOLUSDT",
            timeframe="1m",
        ),
        producer_commit="d" * 40,
    )

    flat = session.execute_bar(
        BarValues(
            open=100,
            high=102,
            low=99,
            close=101,
            volume=1000,
            time=BAR_TIME,
            time_close=BAR_TIME + 60_000,
        ),
        bar_index=0,
        last_bar_index=1,
        strategy_values={
            "strategy.position_size": 0.0,
            "strategy.position_avg_price": 0.0,
            "strategy.position_entry_name": "",
        },
    )
    long = session.execute_bar(
        BarValues(
            open=101,
            high=103,
            low=100,
            close=102,
            volume=1001,
            time=BAR_TIME + 60_000,
            time_close=BAR_TIME + 120_000,
        ),
        bar_index=1,
        last_bar_index=1,
        strategy_values={
            "strategy.position_size": 1.0,
            "strategy.position_avg_price": 101.0,
            "strategy.position_entry_name": "L",
        },
    )

    assert len(flat.intents) == 1
    assert flat.intents[0]["direction"] == "LONG"
    assert long.intents == ()
