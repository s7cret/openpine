"""Transport dependencies must be explicit in every admitted worker policy."""
from pathlib import Path
import json
from unittest.mock import Mock

import pytest

from openpine.runtime import isolated_worker
from tests.rc4_fixtures import admitted_manifest


def test_candidate_and_integration_policy_match_the_current_required_packages():
    template = json.loads((Path(__file__).resolve().parents[1] / "candidates" /
                           "stack-candidate-5.0.0-rc.6.template.json").read_text())
    expected = list(isolated_worker._TRUSTED_NAMES)
    assert template["worker_policy"]["trusted_packages"] == expected
    assert admitted_manifest()["worker_policy"]["trusted_packages"] == expected


def test_old_policy_without_codec_fails_before_staging_or_process_creation(monkeypatch):
    manifest = admitted_manifest()
    manifest["worker_policy"]["trusted_packages"].remove("msgpack")
    stage = Mock()
    monkeypatch.setattr(isolated_worker, "_stage_trusted_packages", stage)
    with pytest.raises(isolated_worker.IsolatedWorkerError, match="trusted package policy"):
        isolated_worker._resolved_worker_policy(manifest)
    stage.assert_not_called()
