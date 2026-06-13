# Release 4.0.0 Checklist

1. Publish/install sibling stack packages at `v4.0.0`.
2. Run backend gate:

```bash
bash scripts/release_gate.sh
```

3. Optionally build a deterministic source zip:

```bash
python -m openpine.distribution build-zip --root . --output openpine-4.0.0.zip --archive-root openpine-4.0.0
```

4. Run cross-repo smoke:

```text
pine source -> pine2ast -> ast2python -> pinelib/backtest-engine -> OpenPine strategy/backtest route smoke
```

5. Run UI checks only if `openpine-ui/` changed.

## Backend Coverage Baseline

This backend hardening release uses an 90% package coverage gate. The threshold now includes broad CLI strategy/data, provider-adapter, exchange-metadata, stream-adapter, TV-corpus, compare, gateway route, execution, websocket, Pine source, strategy, dashboard, scheduler, export, Telegram bot handler, runtime-adapter, gateway-lifespan, optimizer-route, and storage coverage while leaving the web UI untouched. It is still a backend-product baseline; follow-up passes should raise the gate further as CLI, batch, live-runner, and Telegram surfaces are decomposed.


### SQLite storage health

Run `openpine storage migrate` and `openpine storage health` before deployment. The health command verifies the `openpine.sqlite.v4` contract, pending migrations, required indexes, and durable-event compatibility.
