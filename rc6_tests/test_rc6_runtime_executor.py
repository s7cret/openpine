from __future__ import annotations

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_executor import RC6RuntimeExecutor
from pinelib import RuntimeLanguageContext
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext


SOURCE = '//@version=6\nindicator("rc6-runtime")\nplot(close)\n'
COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}


def _artifact_record() -> dict[str, object]:
    result = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="generated_rc6_runtime",
        source_name="rc6-runtime.pine",
        producer_commits=COMMITS,
    )
    assert result.success, result.errors
    return {
        "generated_artifact": result.generated_artifact,
        "python_code": result.python_code,
        "consumer_bundle": result.consumer_bundle,
        "source_map": result.source_map,
        "compile_meta": result.compile_meta,
    }


def _executor() -> RC6RuntimeExecutor:
    return RC6RuntimeExecutor(
        artifact=_artifact_record(),
        language=RuntimeLanguageContext(
            6,
            "2026-09-01",
            "pine-v6",
            "sha256:" + "c" * 64,
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
    )


def _bar(close: float, minute: int) -> BarValues:
    opened = 1_700_000_000_000 + minute * 60_000
    return BarValues(
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000 + minute,
        time=opened,
        time_close=opened + 60_000,
    )


def test_rc6_executor_runs_generated_script_once_per_committed_bar() -> None:
    executor = _executor()

    first = executor.execute_bar(_bar(100, 0), bar_index=0, last_bar_index=1)
    second = executor.execute_bar(_bar(101, 1), bar_index=1, last_bar_index=1)

    assert first.committed and not first.aborted
    assert second.committed and not second.aborted
    assert executor.session.sequence == 1
    assert executor.session.series["close"].read(0) == 101
    assert len(executor.session.visuals.committed) == 2


def test_rc6_executor_binds_v3_entrypoint_identity() -> None:
    executor = _executor()

    assert executor.entrypoint_name == "GeneratedScript"
    assert executor.module_name == "generated_rc6_runtime"
