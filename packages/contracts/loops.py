"""Pinned, bounded loop model; no platform wall-clock timing semantics."""

from dataclasses import fields, is_dataclass
from .model import ForIR, WhileIR, LoopControlIR

ITERATIONS_PER_BAR = 100000
OPERATIONS_PER_BAR = 1000000
CANCELLATION_INTERVAL = 128


def loop_policy(version):
    return {"for": version >= 2, "while": version >= 5,
        "for_final": "each_iteration" if version == 6 else "entry",
        "for_initial_and_step": "entry", "for_direction": "initial_bounds",
        "for_bounds": "inclusive_int", "for_step": "positive_int",
        "na_bound": "no_iteration", "counter_writes": "forbidden_in_this_profile",
        "scope": "local", "control": "nearest_loop_within_function",
        "result": "last_completed_tail", "missing_numeric": "na",
        "missing_bool": "false" if version == 6 else "na",
        "result_types": "numeric_bool_or_flat_numeric_bool_tuple",
        "direct_var_initializer": "first_iteration",
        "state": "written_call_chain_shared_by_iterations",
        "history": "bar_aligned_final_value", "checkpoint": "completed_bar_only",
        "iterations_per_bar": ITERATIONS_PER_BAR, "operations_per_bar": OPERATIONS_PER_BAR,
        "cancellation_interval": CANCELLATION_INTERVAL}


def uses_loops(value):
    if isinstance(value, (ForIR, WhileIR, LoopControlIR)):
        return True
    if is_dataclass(value):
        return any(uses_loops(getattr(value, f.name)) for f in fields(value))
    if isinstance(value, tuple):
        return any(map(uses_loops, value))
    return False


def repeated_call_sites(program):
    from .model import CallIR
    result = set()
    def visit(value, repeated=False):
        repeated = repeated or isinstance(value, (ForIR, WhileIR))
        if repeated and isinstance(value, CallIR):result.add(value.call_site_id)
        if is_dataclass(value):
            for item in fields(value):visit(getattr(value, item.name), repeated)
        elif isinstance(value, tuple):
            for item in value:visit(item, repeated)
    visit(program.statements)
    return result
