"""Small structlog compatibility layer.

OpenPine uses :mod:`structlog` when it is installed.  The backend package should
still be importable in hermetic/offline release gates where optional logging
extras are absent, so this module exposes the tiny subset of the structlog API
that OpenPine uses.
"""

from __future__ import annotations

import logging
from typing import Any

try:  # pragma: no cover - exercised when the real dependency is installed.
    import structlog as _structlog
except ModuleNotFoundError:  # pragma: no cover - fallback is covered by tests.
    _structlog = None


class BoundLoggerAdapter:
    """Logging adapter accepting structlog-style key/value arguments."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def bind(self, **_: Any) -> "BoundLoggerAdapter":
        return self

    def unbind(self, *_: str) -> "BoundLoggerAdapter":
        return self

    def new(self, **_: Any) -> "BoundLoggerAdapter":
        return self

    def _log(self, level: int, event: object, **kwargs: Any) -> None:
        if kwargs:
            self._logger.log(level, "%s %s", event, kwargs)
        else:
            self._logger.log(level, "%s", event)

    def debug(self, event: object, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def info(self, event: object, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: object, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    warn = warning

    def error(self, event: object, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    def exception(self, event: object, **kwargs: Any) -> None:
        self._logger.exception("%s %s", event, kwargs if kwargs else "")


class _FallbackStructlog:
    @staticmethod
    def get_logger(name: str | None = None) -> BoundLoggerAdapter:
        return BoundLoggerAdapter(logging.getLogger(name or "openpine"))


def get_logger(name: str | None = None) -> Any:
    if _structlog is not None:
        return _structlog.get_logger(name)
    return _FallbackStructlog.get_logger(name)
