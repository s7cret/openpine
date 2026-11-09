"""Bind explicitly supplied visual order options without shifting core arguments."""

from dataclasses import replace

from packages.contracts import visual_order
from packages.contracts.wire import digest


def install(catalog):
    catalog.visual_order_contracts = {}
    if 'close' not in catalog.references:
        return
    for contract in tuple(catalog.contracts.values()):
        fields = visual_order.available(contract.operation, catalog.language_version)
        if not fields:
            continue
        suffix = visual_order.OVERLAY_SUFFIX if len(fields) == 2 else visual_order.SUFFIX
        overload_id = contract.overload_id + suffix
        variant = replace(
            contract, overload_id=overload_id, assertion_id=contract.assertion_id + suffix,
            signature_sha256=digest({'base': contract.signature_sha256, 'fields': fields,
                                     'profile': 'openpine.visual-order.1'}),
            parameter_ids=(*contract.parameter_ids, *(f'parameter.{overload_id}.{name}' for name in fields)),
            parameter_types=(*contract.parameter_types, *(('bool',) for name in fields)),
            qualifier_limits=(*contract.qualifier_limits, *(('const',) * len(fields))),
        )
        catalog.contracts[overload_id] = variant
        catalog.visual_order_contracts[contract.overload_id] = variant


def extend(catalog, signature, contract):
    variant = catalog.visual_order_contracts.get(contract.overload_id)
    if variant is None:
        return signature, contract
    fields = visual_order.fields(variant.overload_id)
    parameters = [{'name': name, 'parameter_id': parameter_id, 'requiredness': {'state': 'optional'},
                   'default': {'kind': 'literal', 'value': visual_order.DEFAULTS[name]}}
                  for name, parameter_id in zip(fields, variant.parameter_ids[-len(fields):], strict=True)]
    return {**signature, 'parameters': [*signature['parameters'], *parameters]}, variant


def select(binder, call, signature, contract):
    if 'declaration' not in contract.effects:
        return signature, contract
    named = {argument.name for argument in call.arguments} & visual_order.DEFAULTS.keys()
    if not named:
        return signature, contract
    selected = extend(binder.catalog, signature, contract)
    unsupported = named.difference(visual_order.fields(selected[1].overload_id))
    if unsupported:
        binder.fail('LANGUAGE_INVALID', 'VISUAL_ORDER_VERSION',
                    f'{", ".join(sorted(unsupported))} is unavailable for this Pine signature.', call.span)
    return selected
