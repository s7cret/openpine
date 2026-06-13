"""Timezone helpers used by CLI and API date parsing.

OpenPine persists timestamps in UTC milliseconds, but user-facing date-only
inputs need a product-level default timezone. The default is a fixed UTC+03:00
zone labelled MSK for backwards compatibility with the original CLI behavior.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "UTC+03:00"
DEFAULT_TIMEZONE_LABEL = "MSK"
_ENV_TIMEZONE = "OPENPINE_TIMEZONE"
_UTC_OFFSET_RE = re.compile(
    r"^(?:UTC)?(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$", re.I
)


@dataclass(frozen=True)
class TimezoneSpec:
    """Resolved timezone plus its stable config name and display label."""

    name: str
    label: str
    tz: tzinfo


def _format_utc_offset_name(offset: timedelta) -> str:
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def resolve_timezone(name: str | None = None) -> TimezoneSpec:
    """Resolve an OpenPine timezone setting.

    Supported values:
    - ``UTC``, ``Z``;
    - ``MSK`` as a compatibility alias for fixed ``UTC+03:00``;
    - fixed offsets: ``UTC+3``, ``UTC+03:00``, ``+03:00``;
    - IANA names accepted by :mod:`zoneinfo`, e.g. ``Europe/Moscow``.
    """

    raw = (name or DEFAULT_TIMEZONE).strip()
    upper = raw.upper()
    if upper in {"UTC", "Z"}:
        return TimezoneSpec(name="UTC", label="UTC", tz=timezone.utc)
    if upper == "MSK":
        return TimezoneSpec(
            name=DEFAULT_TIMEZONE,
            label=DEFAULT_TIMEZONE_LABEL,
            tz=timezone(timedelta(hours=3), DEFAULT_TIMEZONE_LABEL),
        )

    match = _UTC_OFFSET_RE.match(raw)
    if match:
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes") or "0")
        if hours > 23 or minutes > 59:
            raise ValueError(f"Invalid timezone offset: {raw}")
        sign = 1 if match.group("sign") == "+" else -1
        offset = sign * timedelta(hours=hours, minutes=minutes)
        name = _format_utc_offset_name(offset)
        label = DEFAULT_TIMEZONE_LABEL if name == DEFAULT_TIMEZONE else name
        return TimezoneSpec(name=name, label=label, tz=timezone(offset, label))

    try:
        zone = ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unsupported timezone: {raw}") from exc
    return TimezoneSpec(name=raw, label=raw, tz=zone)


def configured_timezone(name: str | None = None) -> TimezoneSpec:
    """Resolve the active OpenPine timezone setting.

    Resolution order is explicit argument, ``OPENPINE_TIMEZONE``, YAML config,
    then the product default. The YAML lookup is lazy to avoid config import cycles
    during model validation.
    """

    if name:
        return resolve_timezone(name)
    if env_value := os.environ.get(_ENV_TIMEZONE):
        return resolve_timezone(env_value)
    try:
        from openpine.config.loader import load_config

        return resolve_timezone(load_config().timezone)
    except Exception:
        return resolve_timezone(DEFAULT_TIMEZONE)


def parse_timestamp_ms(
    value: str | None, default: int, *, default_tz: str | None = None
) -> int:
    """Parse ms/s timestamps or ISO strings into UTC milliseconds.

    Naive ISO values are interpreted in the configured timezone rather than
    silently assuming UTC. Numeric values keep the existing OpenPine convention:
    ten-digit values are seconds, larger values are milliseconds.
    """

    if not value:
        return default
    text = value.strip()
    if text.isdigit():
        raw = int(text)
        return raw if raw > 10_000_000_000 else raw * 1000
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=configured_timezone(default_tz).tz)
    return int(parsed.timestamp() * 1000)


def parse_ymd_ms(value: str, *, default_tz: str | None = None) -> int:
    """Parse a ``YYYY-MM-DD`` date at midnight in the configured timezone."""

    parsed = datetime.strptime(value, "%Y-%m-%d").replace(
        tzinfo=configured_timezone(default_tz).tz
    )
    return int(parsed.timestamp() * 1000)


def format_utc_ms(timestamp_ms: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).strftime(fmt)


__all__ = [
    "DEFAULT_TIMEZONE",
    "DEFAULT_TIMEZONE_LABEL",
    "TimezoneSpec",
    "configured_timezone",
    "format_utc_ms",
    "parse_timestamp_ms",
    "parse_ymd_ms",
    "resolve_timezone",
]
