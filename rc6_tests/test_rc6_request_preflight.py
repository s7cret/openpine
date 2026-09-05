"""Literal missing contexts fail before staging; unresolved contexts are not guessed."""

import ast
from unittest.mock import Mock

import pytest

from openpine.runtime import isolated_worker
from openpine.runtime.request_data import admit_request_data
from openpine.runtime.request_requirements import compiled_request_requirements
from openpine.runtime.rc6_config import serialize_engine_config
from rc6_tests.test_rc6_requests import ID, case_for, source_rows
from rc6_tests.test_rc6_worker_admission import _manifest


@pytest.mark.parametrize(
    "call",
    [
        'request.security("MISSING","5",close)',
        f'request.security("{ID}","15",close)',
        'request.security("MISSING","5",close,ignore_invalid_symbol=true)',
        'request.security_lower_tf("MISSING","1",close)',
    ],
)
def test_missing_literal_context_fails_before_worker_staging(tmp_path, monkeypatch, call):
    case, _ = case_for(f"x={call}")
    compiled, context, config = case
    stage, process = Mock(), Mock()
    monkeypatch.setattr(isolated_worker, "_stage_trusted_packages", stage)
    monkeypatch.setattr(isolated_worker.subprocess, "Popen", process)
    with pytest.raises(isolated_worker.IsolatedWorkerError, match="no admitted dataset"):
        isolated_worker.InteractiveWorkerSession(
            compiled.python_code.encode(),
            context,
            ID,
            _manifest(),
            compiled.generated_artifact,
            "sha256:" + "1" * 64,
            tmp_path,
            semantic_profile="strict_5x",
            chart_timeframe="1m",
            engine_config=serialize_engine_config(config, "strict_5x"),
        )
    stage.assert_not_called()
    process.assert_not_called()


def test_empty_literals_resolve_to_chart_identity():
    case, _ = case_for('x=request.security("","",close)', sources=[source_rows("1m")])
    compiled, context, config = case
    (requirement,) = compiled_request_requirements(ast.parse(compiled.python_code), context)
    assert (requirement.symbol, requirement.timeframe) == (ID, "1")
    assert admit_request_data(
        compiled.python_code, serialize_engine_config(config, "strict_5x"), context
    )


def test_dynamic_symbol_is_explicitly_unresolved_not_replaced_with_chart():
    case, _ = case_for(f's=close>0 ? "{ID}" : "other"\nx=request.security(s,"5",close)')
    compiled, context, config = case
    (requirement,) = compiled_request_requirements(ast.parse(compiled.python_code), context)
    assert requirement.symbol is None and requirement.timeframe == "5"
    assert admit_request_data(
        compiled.python_code, serialize_engine_config(config, "strict_5x"), context
    )


def test_ignored_invalid_lower_interval_does_not_require_unused_source():
    case, _ = case_for(
        'x=request.security_lower_tf("MISSING","5",close,ignore_invalid_timeframe=true)'
    )
    compiled, context, config = case
    assert admit_request_data(
        compiled.python_code, serialize_engine_config(config, "strict_5x"), context
    )


def test_nonignored_invalid_lower_interval_fails_at_admission():
    case, _ = case_for(f'x=request.security_lower_tf("{ID}","5",close)')
    compiled, context, config = case
    with pytest.raises(ValueError, match="exceeds chart timeframe"):
        admit_request_data(
            compiled.python_code, serialize_engine_config(config, "strict_5x"), context
        )


def test_nonliteral_ignore_flag_is_not_treated_as_false():
    case, _ = case_for(
        'ignore=true\nx=request.security_lower_tf("MISSING","5",close,ignore_invalid_timeframe=ignore)'
    )
    compiled, context, config = case
    (requirement,) = compiled_request_requirements(ast.parse(compiled.python_code), context)
    assert requirement.ignore_invalid_timeframe is None
    assert admit_request_data(
        compiled.python_code, serialize_engine_config(config, "strict_5x"), context
    )
