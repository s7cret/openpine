"""Shared dispatch/admission facade; older string-only admission stays closed."""
from types import MappingProxyType
from . import scalar_functions as strings,numeric_functions as numbers

OPERATIONS=strings.OPERATIONS|numbers.OPERATIONS

def contracts(version):
    return MappingProxyType({**{c.overload_id:c for c in strings.contracts(version).values()},**numbers.contracts(version)})

def invoke(operation,arguments,budget=None,version=6):
    return (numbers.invoke if operation in numbers.OPERATIONS else strings.invoke)(operation,arguments,budget,version)

def validate_call(node,version,*,allow_numeric=False):
    if allow_numeric and node.operation in numbers.OPERATIONS:return numbers.validate_call(node,version)
    return strings.validate_call(node,version)
