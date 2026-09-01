"""openpine.compile — native RC6 Pine compilation pipeline."""

from __future__ import annotations

from typing import Any

from openpine.compile.native_rc6 import (
    CompileResult,
    CompilerAdapter,
    LibraryAvailability,
    NativeRC6CompilerAdapter,
)


def compile_pipeline(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Load the storage-coupled pipeline only when compilation is persisted."""

    from openpine.compile.pipeline import compile_pipeline as implementation

    return implementation(*args, **kwargs)


__all__ = [
    "CompileResult",
    "CompilerAdapter",
    "LibraryAvailability",
    "NativeRC6CompilerAdapter",
    "compile_pipeline",
]
