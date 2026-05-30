"""Manifest store for strategy manifests."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from openpine.config import OpenPineConfig


class ManifestStore:
    """Stores and retrieves strategy manifests as JSON files."""

    def __init__(self, manifest_dir: Path | None = None) -> None:
        """Initialize manifest store.

        Args:
            manifest_dir: Directory to store manifests. Defaults to config_dir/manifests.
        """
        if manifest_dir is None:
            manifest_dir = OpenPineConfig.load().config_dir / "manifests"
        self.manifest_dir = Path(manifest_dir)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def save_manifest(self, strategy_id: str, manifest_data: dict) -> None:
        """Atomically write a manifest for a strategy.

        Args:
            strategy_id: Unique strategy identifier
            manifest_data: Manifest data as a dict
        """
        target = self.manifest_dir / f"{strategy_id}.json"
        # Atomic write: write to temp file then rename
        fd, tmp_path = tempfile.mkstemp(dir=self.manifest_dir, suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(manifest_data, f, indent=2)
            shutil.move(tmp_path, target)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def get_manifest(self, strategy_id: str) -> dict | None:
        """Read a manifest for a strategy.

        Args:
            strategy_id: Unique strategy identifier

        Returns:
            Manifest data as dict, or None if not found
        """
        path = self.manifest_dir / f"{strategy_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def list_manifests(self) -> list[str]:
        """List all strategy IDs that have manifests.

        Returns:
            List of strategy IDs
        """
        return [p.stem for p in self.manifest_dir.glob("*.json")]
