"""Supported function policy; checked against the admitted offline release."""


def function_policy(version):
    return {"definition_scope": "global", "parameters": "required_scalar_named_or_positional",
        "parameter_writes": "forbidden", "global_writes": "forbidden", "captures": "globals_visible_at_definition",
        "forward_calls": "acyclic", "return": "last_expression", "if_values": "lazy_scalar_branches",
        "missing_numeric_branch": "na", "missing_bool_branch": "false" if version == 6 else "na",
        "missing_string_branch": "requires_else", "specialization": "written_call_chain",
        "history": "bar_aligned_per_instance", "result_qualifier": "all_dependencies",
        "max_definitions": 64, "max_parameters": 16, "max_call_depth": 12, "max_instances": 128,
        "max_expanded_nodes": 8192, "max_ir_depth": 128}
