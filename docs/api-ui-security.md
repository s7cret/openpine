# API and Web UI security operations

OpenPine keeps the API and Web UI reachable on the LAN, but the control plane must not
run unauthenticated.

## Authentication

Production is explicitly fail-closed. Set `OPENPINE_ENV=production` and a
high-entropy bearer token in the required service environment file:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Store it outside Git in the root-readable/user-private service environment as
`OPENPINE_API_TOKEN`. The browser unlock screen keeps the token in `sessionStorage` only;
it is not compiled into the UI bundle and is removed when the browser session ends.

`/health` remains public for service monitoring and exposes only status/version in
production; runtime worker details, OpenAPI, Swagger UI, and ReDoc are disabled there.
Requests under `/api` require
`Authorization: Bearer ***` whenever `OPENPINE_API_TOKEN` is configured. Production must
configure this variable; leaving it unset is supported only for development and tests. Rotate the token by
replacing the environment value and restarting the API service.

## LAN and proxy behavior

The published services intentionally bind to `0.0.0.0:8080` and `0.0.0.0:1888` so another
computer on the private network can connect. The production Web UI proxies same-origin
`/api/*` requests to `127.0.0.1:8080`; this avoids permissive CORS rules.

Client-controlled `X-Forwarded-For` is not trusted. A forwarded client address may be used
only when the socket peer is explicitly listed in `OPENPINE_TRUSTED_PROXY_CIDRS`. The bundled
UI proxy overwrites `X-Forwarded-For` with its socket peer address. For the bundled same-host
proxy, configure `OPENPINE_TRUSTED_PROXY_CIDRS=127.0.0.0/8,::1/128`; do not trust LAN-wide
CIDRs.

The bundled proxy accepts only HTTP origin-form request targets. Absolute-form and
network-path targets are rejected instead of being interpreted as alternate upstream hosts.

The API and UI services must receive the token through a private systemd environment file;
do not place it in the unit template, release directory, shell history, frontend source, or
Git. Use `EnvironmentFile=/path/to/private/openpine-api.env` in a service override with mode
`0600` and include both `OPENPINE_API_TOKEN` and `OPENPINE_TRUSTED_PROXY_CIDRS` there.

## Access history

Both services write structured access events to the user journal without request bodies,
query values, cookies, authorization headers, or WebSocket credentials. Browser WebSocket
clients offer the session token as URL-safe base64 in the
`openpine.bearer.b64.<token>` subprotocol together with the credential-free
`openpine.events.v1` protocol. The server selects only `openpine.events.v1` and never echoes
the credential-bearing value; credentials must never be placed in the URL query:

```bash
journalctl --user -u openpine-ui.service -o cat --since today
journalctl --user -u openpine-api.service -o cat --since today
```

Each event includes timestamp, request ID, client IP, method, normalized path, status,
latency, user agent, and authentication outcome where available. Correlate proxy and API
events by `request_id`.

## Backtest idempotency and cancellation

Backtest launches accept `Idempotency-Key`. A completed key-to-run mapping is retained for
seven days. An incomplete scheduling claim is reclaimable after five minutes so a process
crash cannot block that key forever. Reusing a retained key with a different request body
returns `409`.

Cancellation is terminal only after the isolated spawned worker process group, including
owned descendants, has stopped. A `cancelling` response is non-terminal and must continue
to be polled.

## Production UI

The live service must run `run-production.sh` against a read-only `dist/` directory. Never
serve the published UI with `vite dev`, HMR, or a runtime `npm install`. Build and test once,
then package with:

```bash
npm run build
npm run package:production -- /path/to/new/immutable-ui-candidate
```

The packager atomically creates a self-contained directory containing `dist/`,
`run-production.sh`, `tools/serve-production.mjs`, the systemd template, and a SHA-256
`manifest.json`; it refuses an in-tree destination and makes the completed tree read-only.
Point the systemd unit at that exact candidate only after the complete release gate passes.
