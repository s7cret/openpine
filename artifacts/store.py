"""ArtifactStore — directory-based artifact storage for compiled Pine strategies."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from openpine.config import OpenPineConfig


class ArtifactStore:
    """Directory-based artifact store.

    Layout:
        <config.data_dir>/artifacts/<source_id>/<artifact_id>/
            source.pine
            ast.json
            generated_strategy.py  # successful compile artifacts only
            compile_meta.json
            requirements.json
            diagnostics.log
    """

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            root = OpenPineConfig.load().data_dir / "artifacts"
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _source_dir(self, source_id: str) -> Path:
        return self._root / source_id

    def _artifact_dir(self, source_id: str, artifact_id: str) -> Path:
        return self._source_dir(source_id) / artifact_id

    def save_artifact(
        self,
        artifact_id: str,
        source_id: str,
        params_hash: str,
        python_code: str | None,
        compile_meta: dict,
        source_text: str | None = None,
        ast_json: str | None = None,
        requirements: dict | None = None,
        diagnostics: str = "",
    ) -> Path:
        """Save a compiled artifact to the store.

        Returns the path to the artifact directory.
        """
        artifact_dir = self._artifact_dir(source_id, artifact_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        strategy_path = artifact_dir / "generated_strategy.py"
        if python_code:
            strategy_path.write_text(python_code)
        elif strategy_path.exists():
            strategy_path.unlink()

        if source_text is not None:
            artifact_dir.joinpath("source.pine").write_text(source_text)
        if ast_json is not None:
            artifact_dir.joinpath("ast.json").write_text(ast_json)

        compile_meta.setdefault("artifact_id", artifact_id)
        compile_meta.setdefault("source_id", source_id)
        compile_meta.setdefault("params_hash", params_hash)
        compile_meta.setdefault("schema_version", "openpine.compile_meta.v1")
        artifact_dir.joinpath("compile_meta.json").write_text(
            json.dumps(compile_meta, indent=2)
        )

        if requirements is not None:
            artifact_dir.joinpath("requirements.json").write_text(
                json.dumps(requirements, indent=2)
            )

        artifact_dir.joinpath("diagnostics.log").write_text(diagnostics)

        return artifact_dir

    def get_artifact(self, artifact_id: str, source_id: str) -> dict:
        """Load artifact metadata and content paths."""
        artifact_dir = self._artifact_dir(source_id, artifact_id)
        if not artifact_dir.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")

        meta_path = artifact_dir / "compile_meta.json"
        compile_meta = {}
        if meta_path.exists():
            compile_meta = json.loads(meta_path.read_text())

        return {
            "artifact_id": artifact_id,
            "source_id": source_id,
            "artifact_dir": str(artifact_dir),
            "python_code": (artifact_dir / "generated_strategy.py").read_text()
            if (artifact_dir / "generated_strategy.py").exists()
            else "",
            "ast_json": (artifact_dir / "ast.json").read_text()
            if (artifact_dir / "ast.json").exists()
            else "",
            "source_text": (artifact_dir / "source.pine").read_text()
            if (artifact_dir / "source.pine").exists()
            else "",
            "compile_meta": compile_meta,
        }

    def list_artifacts(self, source_id: str) -> list[dict]:
        """List all artifacts for a given source."""
        source_dir = self._source_dir(source_id)
        if not source_dir.exists():
            return []

        artifacts = []
        for artifact_id in source_dir.iterdir():
            if artifact_id.is_dir():
                try:
                    artifacts.append(self.get_artifact(artifact_id.name, source_id))
                except FileNotFoundError:
                    continue
        return artifacts

    def get_artifact_path(self, artifact_id: str, source_id: str) -> Path:
        """Return the artifact directory path."""
        return self._artifact_dir(source_id, artifact_id)

    def artifact_exists(self, artifact_id: str, source_id: str) -> bool:
        """Check if an artifact exists."""
        return self._artifact_dir(source_id, artifact_id).exists()
