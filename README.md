# OpenPine

OpenPine is the product gateway and web UI for the Pine research stack. It ties together Pine source management, Pine2AST compilation, AST2Python generation, PineLib runtime helpers, Backtest Engine execution, MarketData Provider candles, Optimizer runs, paper/live strategy scheduling, persistent orders, and a Vue dashboard.

The package is intentionally an orchestration boundary. The parser, generator, runtime, backtest engine, market data provider, and optimizer remain independently publishable libraries with their own public contracts.

## What Is Included

- FastAPI gateway under `openpine.gateway` with routes for dashboard, Pine sources, strategies, data, backtests, orders, positions, events, and optimizer operations.
- Vue/Vite UI under `openpine-ui`.
- SQLite-backed local storage for strategies, Pine sources, compiled artifacts, backtest runs, orders, and runtime state.
- Background worker process for market-data catch-up and live/paper mini-backtests without starving the API process.
- Backtest execution in a separate process so long CPU-bound runs do not block UI polling.
- MSK/UTC+3 display formatting in the UI while persisted timestamps stay UTC milliseconds.

## Repository Layout

```text
accounts/          account and API key models
artifacts/         compiled artifact storage
cli/               command-line entry points
compile/           Pine2AST -> AST2Python pipeline adapters
data/              candle storage, providers, refresh orchestration
execution/         paper/live order adapters
gateway/           FastAPI app and API routes
jobs/              local job scheduler models
openpine-ui/       Vue dashboard
orders/            order persistence and models
registry/          strategy registry
state/             trusted runtime snapshot storage policy
storage/           SQLite helpers and migrations
```

## Requirements

- Python 3.11 or newer. The live machine currently uses Python 3.13 successfully.
- Node.js 20 or newer for the UI.
- SQLite, included with Python on normal Linux/macOS installs.
- Network access to exchange APIs when using live market data.

Python runtime dependencies are declared in `pyproject.toml`. UI dependencies are locked in `openpine-ui/package-lock.json`.

## Install

Use the verbose installer to see exactly what is being installed:

```bash
./scripts/install.sh --dev
```

Manual install:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cd openpine-ui
npm ci
npm run build
```

The released package installs the published stack versions declared in `pyproject.toml`:

- `pine2ast==2.17.0`
- `ast2python==2.17.0`
- `pinelib==2.17.0`
- `backtest_engine==2.17.0`
- `marketdata-provider==2.18.0`
- `optimizer==2.17.0`

If local stack libraries are being developed from source, clone them outside this repository and install them first in dependency order:

```bash
python -m pip install -e ../pine2ast
python -m pip install -e ../marketdata-provider
python -m pip install -e ../pinelib
python -m pip install -e ../ast2python
python -m pip install -e ../backtest_engine
python -m pip install -e ../optimizer
```

## Run Locally

Backend:

```bash
OPENPINE_ALLOW_PICKLE_STATE=1 \
python -c "from openpine.gateway.server import create_app; import uvicorn; uvicorn.run(create_app(), host='0.0.0.0', port=8080)"
```

UI:

```bash
cd openpine-ui
npm run dev -- --host 0.0.0.0 --port 1888
```

Open `http://localhost:1888`. The UI proxies `/api` to the gateway on port `8080`.

## Docker Compose

Docker Compose is provided as a publication-ready deployment smoke target:

```bash
docker compose up --build
```

Services:

- `gateway`: OpenPine API on `8080`.
- `ui`: Vite UI on `1888`, proxying to `gateway`.

Runtime state is mounted in named volumes. Source code is copied into images; local caches and SQLite files are not committed.

## Operational Notes

- Persistent timestamps are UTC milliseconds. UI rendering applies display timezone only.
- Pickle resume state is trusted-local only. Set `OPENPINE_ALLOW_PICKLE_STATE=1` only for state you created locally.
- `OPENPINE_ENABLE_BACKGROUND_WORKER=1` is the default. It runs periodic data catch-up and paper mini-backtests in a process outside the API.
- Backtest progress is persisted and exposed via `/api/backtest/progress/{run_id}`.
- Trade notifications are UI-polling based; WebSocket order events from worker processes are not assumed to cross process boundaries.

## Development Gates

```bash
python -m compileall accounts adapters artifacts batch cli compile config data execution gateway jobs orders optimizer registry state storage tests
python -m pytest
python -m ruff check .
cd openpine-ui && npm run build
```

## GitHub Publication Checklist

See `docs/GITHUB_PUBLICATION.md`.

## License

MIT. See `LICENSE`.

## Support / Donations

OpenPine development is independent and MIT-licensed. Donations are optional and help keep the public tooling maintained.

- Telegram: https://t.me/OpenPine
- TON: `UQAyIr2sQ4-_Q5L-4VINcU18khDas5GPbAlYEkQN6S_qzui2`
- SOL: `EbxMUK2W4RGeQZCTRFrdgpEJvnqtyczPZvBrQa1cYJnQ`

Support does not affect license terms, feature access, or project guarantees.
