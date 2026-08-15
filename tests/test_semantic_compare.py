from openpine.compare import compare_semantic_profiles, profile_from_job


def test_missing_profile_is_warning_not_ok() -> None:
    result = compare_semantic_profiles(None, "strict_5x")
    assert result["warning"] is True
    assert result["ok"] is False
    assert result["code"] == "SEMANTIC_PROFILE_MISSING"


def test_different_profiles_are_warning_not_silent_mix() -> None:
    result = compare_semantic_profiles("legacy_4x", "strict_5x")
    assert result["warning"] is True
    assert result["ok"] is False
    assert result["code"] == "SEMANTIC_PROFILE_MISMATCH"
    assert result["left"] == "legacy_4x"
    assert result["right"] == "strict_5x"


def test_matching_profiles_are_ok() -> None:
    result = compare_semantic_profiles("strict_5x", "strict_5x")
    assert result["ok"] is True
    assert result["warning"] is False


def test_profile_is_read_from_job_artifact_ref() -> None:
    job = {"input_artifact_refs": ["art-1", "semantic_profile:legacy_4x"]}
    assert profile_from_job(job) == "legacy_4x"
    assert profile_from_job({"input_artifact_refs": []}) is None
