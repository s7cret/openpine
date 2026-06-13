# Development

Install backend dev dependencies:

```bash
python -m pip install -e '.[dev]'
```

Run backend gates:

```bash
bash scripts/release_gate.sh
```

Manual gates:

```bash
python -m compileall -q openpine tests
python -m ruff check openpine scripts --select F,E9
python -m pytest -q --cov=openpine --cov-report=term
python -m openpine.quality duplicates openpine
python -m openpine.quality architecture openpine --max-lines 4000
python -m openpine.distribution manifest --root .
python -m openpine.release --root .
bash scripts/smoke_import_parse.sh
```

The UI is not part of this backend pass. Run UI checks separately in `openpine-ui/` when changing frontend code.

## Coverage Baseline

The current 4.0 backend gate sets coverage fail-under to 90% after the timezone, database, CLI strategy/data, provider-adapter, exchange-metadata, stream-adapter, TV-corpus, compare, gateway/live-runner/batch, scheduler/dashboard, export, Telegram, runtime-adapter, and gateway-lifespan, state CLI, Telegram polling, storage-adapter, and strategy lifecycle hardening passes. Future backend-only passes should continue raising this while decomposing the legacy CLI, batch runner, live-runner, and Telegram surfaces.


## Timezone-sensitive tests

The backend default timezone is configurable through `timezone` in `.openpine/config.yaml` or `OPENPINE_TIMEZONE`. Release tests keep the default `UTC+03:00`/`MSK` behavior but also cover `UTC` overrides. Avoid hard-coded `timedelta(hours=3)` in new code; use `openpine.timezones.parse_timestamp_ms()` or `parse_ymd_ms()`.


### SQLite storage health

Run `openpine storage migrate` and `openpine storage health` before deployment. The health command verifies the `openpine.sqlite.v4` contract, pending migrations, required indexes, and durable-event compatibility.
