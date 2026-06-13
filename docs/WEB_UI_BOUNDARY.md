# Web UI Boundary

This 4.0 backend hardening pass does not redesign or refactor `openpine-ui/`.

Allowed backend-side changes:

- package layout;
- Python dependencies and version pins;
- SQLite migrations;
- gateway/runtime/storage/data orchestration;
- docs and release scripts.

Frontend changes should be made in a separate UI-focused pass with `npm ci`, `npm run build`, and route/API compatibility checks.
