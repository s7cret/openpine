"""Type relations for the explicitly admitted statement interpreter."""

from packages.contracts import ValueType
from packages.contracts.composite import TupleType,qualified
from packages.contracts.arrays import ARRAY_BASES

QUALIFIERS = ("const", "input", "simple", "series")
SCALARS = ("int", "float", "bool", "string")


def valid_type(value_type):
    if isinstance(value_type,TupleType):
        return (type(value_type.elements) is tuple and 2<=len(value_type.elements)<=16
            and all(type(t) is ValueType and t.base in SCALARS and t.qualifier in QUALIFIERS for t in value_type.elements)
            and len({t.qualifier for t in value_type.elements})==1)
    return isinstance(value_type, ValueType) and value_type.base in (*ARRAY_BASES, *SCALARS, "void") and value_type.qualifier in QUALIFIERS and (value_type.base not in ARRAY_BASES or value_type.qualifier == 'series')


def qualifier(*types):
    return max((value.qualifier for value in types), key=QUALIFIERS.index)


def binary_type(operator, left, right, policy):
    if not valid_type(left) or not valid_type(right) or left.base != right.base:
        raise ValueError("Binary operands must have the same resolved base type")
    base = left.base
    if operator in ("and", "or"):
        if base != "bool":
            raise ValueError("Logical operands must be bool")
    elif operator in ("==", "!="):
        if base not in SCALARS:
            raise ValueError("Equality requires scalar operands")
        base = "bool"
    elif operator in ("<", "<=", ">", ">="):
        if base not in ("int", "float"):
            raise ValueError("Ordering requires numeric operands")
        base = "bool"
    elif operator in ("+", "-", "*", "/", "%"):
        if base not in ("int", "float") and not (base == "string" and operator == "+"):
            raise ValueError("Invalid arithmetic operands")
        if operator == "/" and (base == "float" or policy.integer_division == "float"):
            base = "float"
    else:
        raise ValueError("Unknown binary operator")
    return ValueType(base, qualifier(left, right))


def call_type(contract, types):
    """Recompute result types from the admitted contract, never trust IR claims."""
    rule = contract.result_rule
    if rule == "derived":
        return ValueType(contract.result_type.base, qualifier(*types))
    if rule == "common":
        bases = {t.base for t in types}
        if len(bases) != 1 or not bases <= {"int", "float", "bool"}:
            raise ValueError("Common-result arguments must share a resolved type")
        return ValueType(types[0].base, qualifier(*types))
    if rule == "source_series":
        return ValueType(types[0].base, "series")
    if rule == "fixed_series":
        return qualified(contract.result_type, 'series')
    if rule == 'request_series':return qualified(types[2],'series')
    if rule != "fixed":
        raise ValueError("Unknown result rule")
    return types[contract.result_parameter] if contract.result_parameter is not None else contract.result_type
