"""ArtifactStore — directory-based artifact storage for compiled Pine strategies."""

from __future__ import annotations

import json
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

    @staticmethod
    def _validate_path_component(component: str) -> None:
        component_path = Path(component)
        if (
            not component
            or component_path.is_absolute()
            or component_path.name != component
            or component in {".", ".."}
        ):
            raise ValueError(
                f"Artifact path escapes artifact storage root: {component}"
            )

    def _path_under_root(self, *parts: str) -> Path:
        for part in parts:
            self._validate_path_component(part)
        path = self._root.joinpath(*parts)
        root = self._root.resolve()
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Artifact path escapes artifact storage root: {path}"
            ) from exc
        return path

    def _source_dir(self, source_id: str) -> Path:
        return self._path_under_root(source_id)

    def _artifact_dir(self, source_id: str, artifact_id: str) -> Path:
        return self._path_under_root(source_id, artifact_id)

    @staticmethod
    def _read_optional_text(artifact_dir: Path, filename: str) -> str:
        path = artifact_dir / filename
        return path.read_text() if path.exists() else ""

    @staticmethod
    def _read_compile_meta(artifact_dir: Path, artifact_id: str) -> dict:
        meta_path = artifact_dir / "compile_meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Artifact metadata not found: {artifact_id}")
        return json.loads(meta_path.read_text())

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

        return {
            "artifact_id": artifact_id,
            "source_id": source_id,
            "artifact_dir": str(artifact_dir),
            "python_code": self._read_optional_text(
                artifact_dir, "generated_strategy.py"
            ),
            "ast_json": self._read_optional_text(artifact_dir, "ast.json"),
            "source_text": self._read_optional_text(artifact_dir, "source.pine"),
            "compile_meta": self._read_compile_meta(artifact_dir, artifact_id),
        }

    def list_artifacts(self, source_id: str) -> list[dict]:
        """List all artifacts for a given source."""
        source_dir = self._source_dir(source_id)
        if not source_dir.exists():
            return list()

        artifacts = []
        for artifact_id in sorted(source_dir.iterdir()):
            if artifact_id.is_dir():
                artifacts.append(self.get_artifact(artifact_id.name, source_id))
        return artifacts

    def get_artifact_path(self, artifact_id: str, source_id: str) -> Path:
        """Return the artifact directory path."""
        return self._artifact_dir(source_id, artifact_id)

    def artifact_exists(self, artifact_id: str, source_id: str) -> bool:
        """Check if an artifact exists."""
        return self._artifact_dir(source_id, artifact_id).exists()
