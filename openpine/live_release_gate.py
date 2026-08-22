"""Hard release gate for paper/live execution authority.

The RC.2 mini-backtest runner can synthesize filled orders and is therefore
never admissible for paper or live execution.  This module deliberately has no
environment/config override.  A later reviewed deployment-manifest gate will
replace the constant only after the canonical OrderRouter/BrokerEvent path
passes G7.
"""

from __future__ import annotations

LIVE_RELEASE_ENABLED = False
LIVE_RC_BLOCKED_CODE = "LIVE_RC_BLOCKED"
LIVE_RC_BLOCKED_MESSAGE = "live execution is disabled for this release candidate"


def live_execution_enabled() -> bool:
    """Return the compile-time RC live gate; config/env cannot override it."""

    return LIVE_RELEASE_ENABLED
