"""Extend each admitted entry and close signature with pinned named fields."""
from dataclasses import replace

from packages.contracts import fill_alerts
from packages.contracts.wire import digest
from .specification import SpecError


def install(catalog):
    catalog.fill_alert_calls = {}
    names = fill_alerts.available(catalog.language_version)
    if not names or 'close' not in catalog.references:
        return
    rows = {row['name']: row for row in catalog.snapshot.references[catalog.language_version]['entries']
            if row['category'] == 'functions' and row['name'] in ('strategy.entry', 'strategy.close')}
    for name in ('strategy.entry', 'strategy.close'):
        row = rows.get(name)
        if not row or not set(names) <= {item['name'] for item in row['parameters']}:
            raise SpecError('SPEC_EVIDENCE', f'{name} fill alert parameters differ in Pine v{catalog.language_version}.')
        for group in (catalog.signatures[name], *((mapping[name],) for mapping in (
                catalog.pending_entries, catalog.partial_exits, catalog.order_groups) if name in mapping)):
            for signature, base in group:
                add_variant(catalog, signature, base, names, row)


def add_variant(catalog, signature, base, names, row):
    suffix = fill_alerts.SUFFIXES[len(names)]
    overload = base.overload_id + suffix
    ids = tuple(f'parameter.{overload}.{name}' for name in names)
    parameters = [{'name': name, 'parameter_id': identifier, 'requiredness': {'state': 'optional'},
                   'default': {'kind': 'literal', 'value': fill_alerts.DEFAULTS[name]}}
                  for name, identifier in zip(names, ids, strict=True)]
    contract = replace(base, overload_id=overload, assertion_id=base.assertion_id + suffix,
        signature_sha256=digest({'base': base.signature_sha256, 'reference': digest(row),
                                 'profile': fill_alerts.PROFILE, 'fields': names}),
        parameter_ids=(*base.parameter_ids, *ids),
        parameter_types=(*base.parameter_types, *((fill_alerts.TYPES[name],) for name in names)),
        qualifier_limits=(*base.qualifier_limits, *('series',) * len(names)))
    catalog.contracts[overload] = contract
    catalog.fill_alert_calls[base.overload_id] = ({**signature, 'parameters': [*signature['parameters'], *parameters]}, contract)


def select(binder, call, signature, contract):
    requested = {arg.name for arg in call.arguments} & fill_alerts.DEFAULTS.keys()
    if not requested or contract.operation not in fill_alerts.OPERATIONS:
        return signature, contract
    if not requested <= set(fill_alerts.available(binder.catalog.language_version)):
        binder.fail('LANGUAGE_INVALID', 'FILL_ALERT_VERSION',
                    'Order fill alert arguments are unavailable in this Pine version.', call.span)
    return binder.catalog.fill_alert_calls[contract.overload_id]
