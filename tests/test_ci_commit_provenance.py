"""CI execution must use exact producer commits attested by prepared bundles."""

import pytest

from openpine.verification.execution_identity import clean_environment


_COMPONENTS = ("openpine-contracts", "pine2ast", "ast2python", "pinelib", "backtest_engine", "marketdata-provider", "optimizer")
_PINS = {name: f"{index + 1:x}" * 40 for index, name in enumerate(_COMPONENTS)}
_COMMIT = "a" * 40


def test_clean_environment_forwards_only_explicit_attested_build_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPINE_BUILD_COMMIT", "b" * 40)
    assert "OPENPINE_BUILD_COMMIT" not in clean_environment({}, tmp_path / "plain")
    env = clean_environment({}, tmp_path / "exact", build_commit=_COMMIT)
    assert env["OPENPINE_BUILD_COMMIT"] == _COMMIT
    with pytest.raises(ValueError, match="build commit"):
        clean_environment({}, tmp_path / "invalid", build_commit="not-a-commit")


def test_prepared_lanes_must_agree_on_all_real_producer_commits():
    from openpine.verification.execution_ci import attest_ci_source_commits

    first = {"host_commit": _COMMIT, "source_pins": dict(_PINS)}
    assert attest_ci_source_commits([first, dict(first)]) == {"openpine": _COMMIT, **_PINS}
    with pytest.raises(ValueError, match="producer commits"):
        attest_ci_source_commits([first, {**first, "host_commit": "b" * 40}])
    with pytest.raises(ValueError, match="producer commits"):
        attest_ci_source_commits([first, {**first, "source_pins": {**_PINS, "pinelib": "c" * 40}}])
    with pytest.raises(ValueError, match="producer commits"):
        attest_ci_source_commits([{**first, "host_commit": "unavailable"}])
