"""Observed ABI enforcement for the current support decision."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from openpine.verification.execution_identity import environment_snapshot, validate_python_support

HOST = Path(__file__).resolve().parents[1]
POLICY = json.loads((HOST / 'verification/execution-policy.json').read_text())


def test_current_support_preserves_all_components_and_owner_gates():
    assert len(POLICY['components']) == 8
    assert all(c['pythons'] == ['3.13'] for c in POLICY['components'].values())
    assert set(POLICY['required_gates']['stage-full']) == {
        'branch-reconciliation', 'foundation', 'protected-workers', 'coverage',
        'frontend', 'packages', 'test-performance',
    }
    validate_python_support(POLICY, environment_snapshot())


@pytest.mark.parametrize('field,value', [
    ('implementation', 'PyPy'), ('python', '3.12.9'), ('python', '3.14.0'),
    ('gil_enabled', False), ('gil_enabled', None), ('py_gil_disabled', True),
    ('py_gil_disabled', None), ('soabi', 'cpython-313t-x86_64-linux-gnu'),
    ('soabi', 'cpython-312-x86_64-linux-gnu'), ('soabi', None),
])
def test_current_support_rejects_foreign_or_unobserved_abi(field, value):
    observed = deepcopy(environment_snapshot())
    observed[field] = value
    with pytest.raises(ValueError, match='ordinary CPython 3.13'):
        validate_python_support(POLICY, observed)


def test_generic_fixture_matrix_remains_explicitly_configurable():
    validate_python_support({'components': {'tiny': {'pythons': ['3.12']}}}, {})
