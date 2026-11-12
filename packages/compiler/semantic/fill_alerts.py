"""Extend admitted order signatures with pinned named alert fields."""
from dataclasses import replace

from packages.contracts import fill_alerts
from packages.contracts.wire import digest
from .specification import SpecError


COMMANDS = ('strategy.entry', 'strategy.close', 'strategy.order', 'strategy.close_all', 'strategy.exit')


def install(catalog):
    catalog.fill_alert_calls = {}
    version = catalog.language_version
    if version < 4 or 'close' not in catalog.references:
        return
    references = catalog.snapshot.references[version]['entries']
    for name in COMMANDS:
        rows = [row for row in references if row['category'] == 'functions' and row['name'] == name]
        variants = {contract.overload_id: (signature, contract) for signature, contract in (
            *catalog.signatures[name], *(mapping[name] for mapping in
                (catalog.pending_entries, catalog.partial_exits, catalog.order_groups, catalog.trailing) if name in mapping))}
        for signature, base in variants.values():
            names = fill_alerts.available(version, base.operation)
            matched = [row for row in rows if set(names) <= {item['name'] for item in row['parameters']}]
            if len(matched) != 1:
                raise SpecError('SPEC_EVIDENCE', f'{name} fill alert parameters differ in Pine v{version}.')
            if name == 'strategy.close_all' and version == 5:
                legacy, = [row for row in rows if row not in matched
                           and any(item['name'] == fill_alerts.MESSAGE for item in row['parameters'])]
                add_variant(catalog, signature, base, (fill_alerts.MESSAGE,), legacy)
            add_variant(catalog, signature, base, names, matched[0])


def add_variant(catalog, signature, base, names, row):
    suffix = fill_alerts.SUFFIXES[names]
    overload = base.overload_id + suffix
    ids = tuple(f'parameter.{overload}.{name}' for name in names)
    parameters = [{'name': name, 'parameter_id': identifier, 'requiredness': {'state': 'optional'},
                   'default': ({'kind': 'na'} if fill_alerts.DEFAULTS[name] is None
                               else {'kind': 'literal', 'value': fill_alerts.DEFAULTS[name]})}
                  for name, identifier in zip(names, ids, strict=True)]
    contract = replace(base, overload_id=overload, assertion_id=base.assertion_id + suffix,
        signature_sha256=digest({'base': base.signature_sha256, 'reference': digest(row),
                                 'profile': (fill_alerts.EXTENDED_PROFILE if base.operation in fill_alerts.EXTENDED_OPERATIONS
                                             else fill_alerts.PROFILE), 'fields': names}),
        parameter_ids=(*base.parameter_ids, *ids),
        parameter_types=(*base.parameter_types, *(('bool',) if name == fill_alerts.DISABLE else ('string',) for name in names)),
        qualifier_limits=(*base.qualifier_limits, *('series',) * len(names)))
    catalog.contracts[overload] = contract
    catalog.fill_alert_calls.setdefault(base.overload_id, []).append(
        ({**signature, 'parameters': [*signature['parameters'], *parameters]}, contract))


def select(binder, call, signature, contract):
    requested = {arg.name for arg in call.arguments} & fill_alerts.DEFAULTS.keys()
    if not requested or contract.operation not in fill_alerts.OPERATIONS:
        return signature, contract
    if not requested <= set(fill_alerts.available(binder.catalog.language_version, contract.operation)):
        binder.fail('LANGUAGE_INVALID', 'FILL_ALERT_VERSION',
                    'Order fill alert arguments are unavailable in this Pine version.', call.span)
    return next(pair for pair in binder.catalog.fill_alert_calls[contract.overload_id]
                if requested <= set(fill_alerts.fields(pair[1])))
