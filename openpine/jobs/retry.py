"""Retry policy with exponential backoff."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetryPolicy:
    """Exponential backoff retry policy.

    Usage:
        policy = RetryPolicy(max_attempts=3, base_delay_seconds=1.0)
        delay = policy.get_delay(attempt=1)  # 1.0s
        delay = policy.get_delay(attempt=2)  # 2.0s
        delay = policy.get_delay(attempt=3)  # 4.0s
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 60.0

    def get_delay(self, attempt: int) -> float:
        """Return delay in seconds for the given attempt (1-indexed).

        Uses exponential backoff capped at max_delay_seconds:
            delay = min(base * (multiplier ** (attempt - 1)), max_delay)
        """
        if attempt <= 0:
            return 0.0
        delay = self.base_delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_delay_seconds)

    def should_retry(self, attempt: int) -> bool:
        """Return True if another attempt is allowed."""
        return attempt < self.max_attempts
