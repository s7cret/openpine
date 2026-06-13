# Architecture

OpenPine is a product gateway/orchestrator, not a compiler/runtime replacement.

## Backend Responsibilities

- Persist Pine sources, compiled artifacts, strategies, orders, backtest runs, and state snapshots.
- Call `pine2ast` and `ast2python` through `openpine.compile`.
- Run generated strategies through `backtest-engine` and `pinelib` boundaries.
- Request candles/footprints from `marketdata-provider` through stable contracts.
- Start optimizer jobs through the standalone `optimizer` package.
- Expose FastAPI routes under `openpine.gateway`.
- Coordinate local jobs/workers and paper/live execution adapters.

## 4.0 Layout Change

The Python code now lives under `openpine/`. This removes the older `package-dir = {"openpine" = "."}` shape, so a source checkout can import `openpine` without editable install tricks.

## Current Large Surfaces

Some product surfaces remain intentionally large after the first 4.0 pass:

- `openpine/cli/main.py` keeps legacy command compatibility.
- `openpine/batch/runner.py` keeps the TradingView corpus/batch contract.
- Gateway route modules remain grouped by product area.

The release gate tracks these through an architecture budget rather than pretending they are already fully decomposed.

## Timezone boundary

Storage contracts use UTC milliseconds. The configurable default timezone is applied only when parsing user-facing date-only or naive ISO inputs. This keeps persistence deterministic while allowing deployments to select `UTC`, `UTC+03:00`/`MSK`, or an IANA timezone for operator workflows.
