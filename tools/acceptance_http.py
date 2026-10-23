"""Bounded HTTP reads for acceptance while the coordinator is doing work."""

import json
import time
import urllib.error
import urllib.request


def request_json(base, token, path, payload=None):
    request = urllib.request.Request(base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-OpenPine-Token": token})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            error.close()
            # Busy reads are explicitly retryable. Never repeat a submission or
            # hide storage, authorization, validation or protocol failures.
            busy = False
            if payload is None and error.code == 503:
                try:
                    diagnostics = json.loads(body).get("diagnostics", [])
                    busy = bool(diagnostics) and all(d.get("code") == "COORDINATOR_BUSY" for d in diagnostics)
                except (ValueError, TypeError, AttributeError):
                    pass
            if busy and attempt < 7:
                time.sleep(min(.8, .05 * 2**attempt))
                continue
            raise AssertionError(f"HTTP {path}: {error.code} {body}") from error
