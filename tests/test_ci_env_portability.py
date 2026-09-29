"""CI environment creation must work with relocatable Python installations."""

import inspect

from openpine.verification import execution_ci


def test_prepare_and_restore_symlink_the_linux_interpreter():
    # uv-managed CPython has an embedded /install prefix when copied into a venv.
    for operation in (execution_ci.prepare, execution_ci.restore):
        source = inspect.getsource(operation)
        assert "venv.EnvBuilder(with_pip=True, symlinks=True)" in source
