"""Type relations for the explicitly admitted statement interpreter."""

from packages.contracts import ValueType

QUALIFIERS = ("const", "input", "simple", "series")
SCALARS = ("int", "float", "bool", "string")


def valid_type(value_type):
    return isinstance(value_type, ValueType) and value_type.base in (*SCALARS, "void") and value_type.qualifier in QUALIFIERS


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
    elif operator in ("+", "-", "*", "/"):
        if base not in ("int", "float") and not (base == "string" and operator == "+"):
            raise ValueError("Invalid arithmetic operands")
        if operator == "/" and (base == "float" or policy.integer_division == "float"):
            base = "float"
    else:
        raise ValueError("Unknown binary operator")
    return ValueType(base, qualifier(left, right))
