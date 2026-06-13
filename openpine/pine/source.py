"""Pine source domain model — section 5.1."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PineSource:
    """Pine source — raw .pine script as logic template.

    One PineSource can spawn many StrategyInstances via different
    CompileArtifacts and parameter combinations.
    """

    id: str
    name: str
    source_text: str
    source_path: str | None = None
    source_hash: str | None = None
    version: str = "1.0.0"
    source_type: str = "strategy"  # strategy | indicator | library | unknown
    active_artifact_id: str | None = None
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "id": self.id,
            "name": self.name,
            "source_text": self.source_text,
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "version": self.version,
            "source_type": self.source_type,
            "active_artifact_id": self.active_artifact_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PineSource:
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            name=data["name"],
            source_text=data["source_text"],
            source_path=data.get("source_path"),
            source_hash=data.get("source_hash"),
            version=data.get("version", "1.0.0"),
            source_type=data.get("source_type", "strategy"),
            active_artifact_id=data.get("active_artifact_id"),
            created_at=data.get("created_at", int(time.time() * 1000)),
            updated_at=data.get("updated_at", int(time.time() * 1000)),
        )
