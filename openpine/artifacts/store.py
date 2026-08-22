"""ArtifactStore — directory-based artifact storage for compiled Pine strategies."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

from openpine_contracts import validate_payload, verify_content_hash

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
        payload = json.loads(meta_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"Artifact metadata is malformed: {artifact_id}")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _read_optional_json(artifact_dir: Path, filename: str) -> dict | list | None:
        path = artifact_dir / filename
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, (dict, list)):
            raise ValueError(f"Artifact JSON is malformed: {filename}")
        return cast(dict[str, Any] | list[Any], payload)

    @staticmethod
    def artifact_id_for_envelope(generated_artifact: dict[str, Any]) -> str:
        validate_payload("openpine.generated_artifact.v2", generated_artifact)
        if not verify_content_hash(
            generated_artifact, schema_id="openpine.generated_artifact.v2"
        ):
            raise ValueError("generated artifact content hash is invalid")
        digest = str(generated_artifact["content_hash"]).split(":", 1)[1]
        return f"art_{digest[:16]}"

    @staticmethod
    def _verify_sealed_payload(
        *,
        artifact_id: str,
        python_code: str,
        source_text: str,
        ast_json: str,
        source_map: list[dict[str, Any]],
        generated_artifact: dict[str, Any],
        frontend_artifact: dict[str, Any] | None,
        support_profile: dict[str, Any] | None,
        ast_artifact: dict[str, Any] | None,
    ) -> None:
        from ast2python.artifact import _digest

        expected_id = ArtifactStore.artifact_id_for_envelope(generated_artifact)
        if artifact_id != expected_id:
            raise ValueError(
                f"artifact_id does not match sealed content identity: {artifact_id} != {expected_id}"
            )
        contract = "openpine.generated_artifact.v2"
        if generated_artifact["source_hash"] != _digest(source_text, contract):
            raise ValueError("generated artifact source hash does not match source bytes")
        if generated_artifact["emitted_module_hash"] != _digest(python_code, contract):
            raise ValueError("generated artifact emitted module hash does not match Python bytes")
        if generated_artifact["source_map_hash"] != _digest(
            {"entries": source_map}, contract
        ):
            raise ValueError("generated artifact source map hash does not match source map")

        parsed_ast = json.loads(ast_json)
        if ast_artifact is None:
            expected_ast_hash = _digest(parsed_ast, contract)
        else:
            validate_payload("pine.ast.v1", ast_artifact)
            if not verify_content_hash(ast_artifact, schema_id="pine.ast.v1"):
                raise ValueError("AST artifact content hash is invalid")
            expected_ast_hash = ast_artifact["content_hash"]
        if generated_artifact["ast_hash"] != expected_ast_hash:
            raise ValueError("generated artifact AST hash does not match AST artifact")

        for payload, schema_id, field, label in (
            (
                frontend_artifact,
                "openpine.frontend.v2",
                "frontend_artifact_hash",
                "frontend artifact",
            ),
            (
                support_profile,
                "openpine.support_profile.v2",
                "support_profile_hash",
                "support profile",
            ),
        ):
            if payload is None:
                continue
            validate_payload(schema_id, payload)
            if not verify_content_hash(payload, schema_id=schema_id):
                raise ValueError(f"{label} content hash is invalid")
            if generated_artifact[field] != payload["content_hash"]:
                raise ValueError(f"generated artifact {label} hash does not match")

    def _save_sealed_artifact(
        self,
        *,
        artifact_id: str,
        source_id: str,
        params_hash: str,
        python_code: str,
        compile_meta: dict,
        source_text: str,
        ast_json: str,
        source_map: list[dict[str, Any]],
        generated_artifact: dict[str, Any],
        frontend_artifact: dict[str, Any] | None,
        support_profile: dict[str, Any] | None,
        ast_artifact: dict[str, Any] | None,
        requirements: dict | None,
        diagnostics: str,
    ) -> Path:
        self._verify_sealed_payload(
            artifact_id=artifact_id,
            python_code=python_code,
            source_text=source_text,
            ast_json=ast_json,
            source_map=source_map,
            generated_artifact=generated_artifact,
            frontend_artifact=frontend_artifact,
            support_profile=support_profile,
            ast_artifact=ast_artifact,
        )
        artifact_dir = self._artifact_dir(source_id, artifact_id)
        if artifact_dir.exists():
            existing = self.get_artifact(artifact_id, source_id)
            if existing.get("generated_artifact") == generated_artifact:
                return artifact_dir
            raise FileExistsError(f"sealed artifact already exists with different bytes: {artifact_id}")

        source_dir = self._source_dir(source_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{artifact_id}.", dir=source_dir))
        meta = dict(compile_meta)
        meta.setdefault("artifact_id", artifact_id)
        meta.setdefault("source_id", source_id)
        meta.setdefault("params_hash", params_hash)
        meta.setdefault("schema_version", "openpine.compile_meta.v1")
        try:
            temp_dir.joinpath("generated_strategy.py").write_text(
                python_code, encoding="utf-8"
            )
            temp_dir.joinpath("source.pine").write_text(source_text, encoding="utf-8")
            temp_dir.joinpath("ast.json").write_text(ast_json, encoding="utf-8")
            for filename, payload in (
                ("source_map.json", source_map),
                ("generated_artifact.json", generated_artifact),
                ("frontend_artifact.json", frontend_artifact),
                ("support_profile.json", support_profile),
                ("ast_artifact.json", ast_artifact),
                ("requirements.json", requirements),
            ):
                if payload is not None:
                    temp_dir.joinpath(filename).write_text(
                        json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
            temp_dir.joinpath("compile_meta.json").write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temp_dir.joinpath("diagnostics.log").write_text(
                diagnostics, encoding="utf-8"
            )
            temp_dir.replace(artifact_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return artifact_dir

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
        source_map: list[dict[str, Any]] | None = None,
        generated_artifact: dict[str, Any] | None = None,
        frontend_artifact: dict[str, Any] | None = None,
        support_profile: dict[str, Any] | None = None,
        ast_artifact: dict[str, Any] | None = None,
    ) -> Path:
        """Save a compiled artifact to the store.

        Returns the path to the artifact directory.
        """
        if generated_artifact is not None:
            if not python_code or source_text is None or ast_json is None or source_map is None:
                raise ValueError(
                    "sealed artifact save requires Python, source, AST, and source map bytes"
                )
            return self._save_sealed_artifact(
                artifact_id=artifact_id,
                source_id=source_id,
                params_hash=params_hash,
                python_code=python_code,
                compile_meta=compile_meta,
                source_text=source_text,
                ast_json=ast_json,
                source_map=source_map,
                generated_artifact=generated_artifact,
                frontend_artifact=frontend_artifact,
                support_profile=support_profile,
                ast_artifact=ast_artifact,
                requirements=requirements,
                diagnostics=diagnostics,
            )

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

        python_code = self._read_optional_text(artifact_dir, "generated_strategy.py")
        ast_json = self._read_optional_text(artifact_dir, "ast.json")
        source_text = self._read_optional_text(artifact_dir, "source.pine")
        source_map = self._read_optional_json(artifact_dir, "source_map.json")
        generated_artifact = self._read_optional_json(
            artifact_dir, "generated_artifact.json"
        )
        frontend_artifact = self._read_optional_json(
            artifact_dir, "frontend_artifact.json"
        )
        support_profile = self._read_optional_json(artifact_dir, "support_profile.json")
        ast_artifact = self._read_optional_json(artifact_dir, "ast_artifact.json")
        if generated_artifact is not None:
            if not isinstance(generated_artifact, dict) or not isinstance(source_map, list):
                raise ValueError(f"sealed artifact files are malformed: {artifact_id}")
            if not all(isinstance(item, dict) for item in source_map):
                raise ValueError(f"sealed source map is malformed: {artifact_id}")
            for payload, label in (
                (frontend_artifact, "frontend artifact"),
                (support_profile, "support profile"),
                (ast_artifact, "AST artifact"),
            ):
                if payload is not None and not isinstance(payload, dict):
                    raise ValueError(f"sealed {label} is malformed: {artifact_id}")
            frontend_mapping = cast(dict[str, Any] | None, frontend_artifact)
            support_mapping = cast(dict[str, Any] | None, support_profile)
            ast_mapping = cast(dict[str, Any] | None, ast_artifact)
            self._verify_sealed_payload(
                artifact_id=artifact_id,
                python_code=python_code,
                source_text=source_text,
                ast_json=ast_json,
                source_map=source_map,
                generated_artifact=generated_artifact,
                frontend_artifact=frontend_mapping,
                support_profile=support_mapping,
                ast_artifact=ast_mapping,
            )

        return {
            "artifact_id": artifact_id,
            "source_id": source_id,
            "artifact_dir": str(artifact_dir),
            "python_code": python_code,
            "ast_json": ast_json,
            "source_text": source_text,
            "compile_meta": self._read_compile_meta(artifact_dir, artifact_id),
            "source_map": source_map,
            "generated_artifact": generated_artifact,
            "frontend_artifact": frontend_artifact,
            "support_profile": support_profile,
            "ast_artifact": ast_artifact,
        }

    def list_artifacts(self, source_id: str) -> list[dict]:
        """List all artifacts for a given source."""
        source_dir = self._source_dir(source_id)
        if not source_dir.exists():
            return []

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
