"""openpine.daemon — service lifecycle management.

Section 19 of OpenPine TZ v3.

Exports:
    DaemonService: base service with lifecycle management.
    ServiceState: service state enum.
"""

from openpine.daemon.service import DaemonService, ServiceState

__all__ = [
    "DaemonService",
    "ServiceState",
]
