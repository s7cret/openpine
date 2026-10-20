"""Deterministic scope identities and conservative function result qualifiers."""

from dataclasses import fields, is_dataclass

QUALIFIERS = ("const", "input", "simple", "series")


def site_id(kind, span, namespace=""):
    return f"{kind}:{namespace}{span.start}:{span.end}"


def branch_scope(span, branch, namespace=""):
    return f"scope:{namespace}{span.start}:{branch}"


def dependency_qualifier(*values):
    result = "const"

    def visit(value):
        nonlocal result
        value_type = getattr(value, "value_type", None)
        if value_type is not None and value_type.base != "void":
            result = max((result, value_type.qualifier), key=QUALIFIERS.index)
        if is_dataclass(value):
            for item in fields(value):
                visit(getattr(value, item.name))
        elif isinstance(value, tuple):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return result
