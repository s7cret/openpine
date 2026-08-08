"""State errors — section 30.8, 33.2."""

from __future__ import annotations


class StateError(Exception):
    """Base state error."""



class SnapshotNotFoundError(StateError):
    """Snapshot does not exist or is not accessible."""



class InvalidSnapshotError(StateError):
    """Snapshot checksum mismatch, corrupt payload, or incompatible schema."""



class StateInconsistencyError(StateError):
    """Section 30.8: state inconsistency after data repair requiring rebuild."""
