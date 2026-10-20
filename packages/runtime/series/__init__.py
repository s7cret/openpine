"""EMA recurrence and the precise admitted rule forms this kernel implements."""

from dataclasses import dataclass


def expression(operation, *arguments):
    return {"kind": "operation", "operation": operation, "arguments": list(arguments)}


def argument(name):
    return {"kind": "argument", "name": name}


def literal(value):
    return {"kind": "literal", "value": value}


SUPPORTED_RULES = {
    "rule.ema": {
        "alpha": expression("divide", literal(2), expression("add", argument("length"), literal(1))),
        "update": expression("add", expression("multiply", argument("alpha"), argument("source")),
                             expression("multiply", expression("subtract", literal(1), argument("alpha")), argument("previous"))),
        "seed": "first_non_na_source", "first_defined_result": "first_non_na_source",
        "na_policy": "ignore_source_na", "minimum_history": 1, "convergence": "asymptotic",
    },
    "rule.ema.length": {"dynamic_phase": "runtime", "min_exclusive": 0, "parameter_name": "length"},
    "rule.state.call_site_context": {"identity": ["call_site", "requested_context"], "var": "first_execution", "varip": "intrabar_persistent"},
    "rule.context.current": {"default": "current_context"},
    "rule.execution.skipped_call": {"skipped_call": "no_update", "v5_and_or": "strict", "v6_and_or": "lazy"},
}


class KernelError(ValueError):
    def __init__(self, code, message, rule_id):
        self.code, self.rule_id = code, rule_id
        super().__init__(message)


@dataclass
class EMAState:
    previous: float | None = None

    def update(self, source: float | None, length: int) -> float | None:
        if length <= 0:
            raise KernelError("EMA_LENGTH", "EMA length must be greater than zero.", "rule.ema.length")
        if source is None:
            return self.previous
        if self.previous is None:
            self.previous = source
        else:
            alpha = 2.0 / (length + 1)
            self.previous = alpha * source + (1.0 - alpha) * self.previous
        return self.previous
