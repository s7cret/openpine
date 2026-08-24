"""Load the immutable admitted stack manifest for worker policy decisions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_HASH = re.compile(r"^sha256:(?!0{64}$)[0-9a-f]{64}$")


class AdmittedManifestError(RuntimeError):
    pass


def load_admitted_manifest(path: str | Path | None = None) -> dict[str, Any]:
    raw_path = path or os.environ.get("OPENPINE_CANDIDATE_MANIFEST")
    if not raw_path:
        raise AdmittedManifestError("OPENPINE_CANDIDATE_MANIFEST is required")
    candidate_path = Path(raw_path)
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmittedManifestError("admitted candidate manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("stage") != "wheel-bound":
        raise AdmittedManifestError("wheel-bound admitted candidate manifest is required")
    manifest_hash = payload.get("manifest_hash")
    if not isinstance(manifest_hash, str) or _HASH.fullmatch(manifest_hash) is None:
        raise AdmittedManifestError("admitted candidate manifest hash is invalid")
    if payload.get("not_a_release") is not True:
        raise AdmittedManifestError("candidate manifest must remain not_a_release")
    return payload


__all__ = ["AdmittedManifestError", "load_admitted_manifest"]
