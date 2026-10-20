"""Finite scalar kernels with explicit NA, conversion and rounding policies."""

from decimal import Decimal, ROUND_HALF_DOWN, ROUND_HALF_UP, localcontext
from packages.runtime.series import KernelError


def missing(arguments, context):
    return arguments[0] is None


def replace_missing(arguments, context):
    return arguments[1] if arguments[0] is None else arguments[0]


def cast_int(arguments, context):
    return None if arguments[0] is None else int(arguments[0])


def cast_float(arguments, context):
    return None if arguments[0] is None else float(arguments[0])


def minimum(arguments, context):
    return None if None in arguments else min(arguments)


def maximum(arguments, context):
    return None if None in arguments else max(arguments)


def rounded(arguments, context):
    source = arguments[0]
    precision = arguments[1] if len(arguments) == 2 else 0
    if type(precision) is not int or not -15 <= precision <= 15:
        raise KernelError("ROUND_PRECISION", "Round precision must be a defined int in -15..15.", "rule.numeric.round")
    if source is None:
        return None
    with localcontext() as decimal_context:
        decimal_context.prec = 400
        value = Decimal(str(source))
        result = value.quantize(Decimal(1).scaleb(-precision),
            rounding=ROUND_HALF_DOWN if value.is_signed() else ROUND_HALF_UP)
    return int(result) if len(arguments) == 1 else float(result)
