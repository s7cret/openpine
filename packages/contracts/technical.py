"""Bounded technical-analysis contracts, independent of compiler and runtime."""


def technical_policy(version):
    return {"language_version": version, "length": "simple_int_1_100000",
        "rma_seed": "sma_first_length_non_na", "rma_alpha": "1/length",
        "source_na": "skip_and_hold", "tr_previous": "previous_physical_bar_close",
        "tr_missing_previous": "high_low_if_handle_na_else_na", "atr": "rma_tr_true",
        "rsi_changes": "consecutive_defined_sources", "rsi_seed": "length_defined_deltas",
        "rsi_zero_loss": "100_if_gain_positive", "rsi_zero_gain": "0_if_loss_positive",
        "rsi_both_zero": "na", "extrema_window": "last_length_reached_calls_including_na",
        "extrema_na": "ignore_candidates", "extrema_warmup": "available_defined_values",
        "skipped_call": "no_state_update", "state_owner": "written_call_and_context",
        "length_change": "reject", "memory_cells": 1000000}


def technical_apis(version):
    prefix = "" if version < 5 else "ta."
    source, length = ("x", "y") if version < 3 else ("source", "length")
    def parameter(name, base, qualifier="series"):
        return {"name": name, "types": [base], "qualifier": qualifier, "default": None}
    def api(name, suffix, operation, parameters, implicit=(), reference=False):
        return {"id": f"openpine.technical.v{version}.{suffix}", "name": prefix+name,
            "operation": operation, "parameters": parameters, "implicit": list(implicit),
            "reference": reference, "named": version >= 3, "result": "float",
            "effects": ["reads_history"] + ([] if operation == "series.tr" else ["updates_state"])}
    market = ("high", "low", "previous_close")
    result = [api("rma", "rma", "series.rma", [parameter(source,"float"),parameter(length,"int","simple")]),
        api("rsi", "rsi", "series.rsi", [parameter(source,"float"),parameter(length,"int","simple")]),
        api("atr", "atr", "series.atr", [parameter("length","int","simple")], market),
        api("tr", "tr.function", "series.tr", [parameter("handle_na","bool","simple")], market),
        api("tr", "tr.variable", "series.tr", [], (*market,"false"), True)]
    for name, slot in (("highest","high"),("lowest","low")):
        result += [api(name, name+".source", "series."+name,
            [parameter("source","float"),parameter("length","int","simple")]),
            api(name, name+".default", "series."+name, [parameter("length","int","simple")],(slot,))]
    return result


def validate_intrinsic(node, version):
    """Frozen IR cannot replace the compiler-supplied physical market inputs."""
    from .model import ReferenceIR, HistoryIR, LiteralIR
    selected=next((a for a in technical_apis(version) if node.overload_id=='overload.'+a['id']),None)
    if selected is None:return
    for position,name in enumerate(selected['implicit']):
        argument=node.arguments[position];value=argument.expression
        if argument.origin!='default' or argument.source_index is not None:
            raise ValueError('Technical market arguments must have implicit origin.')
        if name=='false':
            valid=isinstance(value,LiteralIR) and value.value is False
        elif name=='previous_close':
            valid=(isinstance(value,HistoryIR) and isinstance(value.operand,ReferenceIR)
                and value.operand.contract.slot=='close' and isinstance(value.offset,LiteralIR)
                and type(value.offset.value) is int and value.offset.value==1)
        else:valid=isinstance(value,ReferenceIR) and value.contract.slot==name
        if not valid:raise ValueError('Technical implicit arguments differ from physical market inputs.')
