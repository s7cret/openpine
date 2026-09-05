"""Protocol operations actually implemented by the isolated worker.

Generated-session checkpoint export is not a worker-protocol resume operation.
Keep it out of the advertised/requested protocol feature set until end-to-end
broker, transport and output-cursor restoration exists.
"""

from __future__ import annotations

WORKER_CAPABILITIES = ("closed_bar",)


def validate_requested_capabilities(value: object) -> None:
    if (
        not isinstance(value, list)
        or any(type(item) is not str for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("worker capabilities must be a unique string list")
    unknown = set(value).difference(WORKER_CAPABILITIES)
    if unknown:
        raise ValueError(f"unsupported worker protocol capabilities: {sorted(unknown)}")


def require_worker_capabilities(value: object) -> None:
    if (
        not isinstance(value, list)
        or any(type(item) is not str for item in value)
        or len(set(value)) != len(value)
        or not set(WORKER_CAPABILITIES).issubset(value)
    ):
        raise ValueError("worker does not advertise the required protocol capabilities")
