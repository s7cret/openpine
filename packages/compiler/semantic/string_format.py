"""Expose the bounded positional str.format family through ordinary calls."""

from packages.contracts.string_format import MAX_VALUES, NAME, VERSIONS, contracts, parameter_names

from .specification import SpecError


def install(catalog):
    version = catalog.language_version
    if version not in VERSIONS or 'close' not in catalog.references:
        return
    entries = catalog.snapshot.references[version]['entries']
    rows = [row for row in entries if row['name'] == NAME and row['category'] == 'functions']
    results = [['string', 'series[string]']] if version == 4 else [['simple string'], ['series string']]
    if (sorted(row['result_type_spellings'] for row in rows) != sorted(results)
            or any([parameter['name'] for parameter in row['parameters']] != ['formatString', 'arg0, arg1, ...']
                   for row in rows)):
        raise SpecError('SPEC_EVIDENCE', f'String format signatures differ in Pine v{version}.')

    candidates = []
    for count, contract in enumerate(contracts(version).values()):
        parameters = [{'name': name, 'parameter_id': identifier, 'requiredness': {'state': 'required'}}
                      for name, identifier in zip(parameter_names(count), contract.parameter_ids, strict=True)]
        candidates.append(({'parameters': parameters, 'binding': {'named': False}, 'effects': []}, contract))
        catalog.contracts[contract.overload_id] = contract
    catalog.signatures[NAME] = candidates


def select(binder, call, candidates):
    if not call.arguments:
        binder.fail('LANGUAGE_INVALID', 'MISSING_ARGUMENT', "Missing argument 'formatString'.", call.span)
    count = len(call.arguments) - 1
    if count > MAX_VALUES:
        binder.fail('PROFILE_UNCOVERED', 'STRING_FORMAT_ARITY',
                    f'str.format supports up to {MAX_VALUES} substitution values.', call.span)
    return (candidates[count],)
