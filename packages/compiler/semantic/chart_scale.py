"""Select a declaration's additive chart-scale contract."""

from dataclasses import replace

from packages.compiler.frontend import parser
from packages.contracts import chart_scale as scale
from packages.contracts.wire import digest


def install(catalog):
    catalog.scale_contracts = {}
    if 'close' not in catalog.references:
        return
    catalog.color_constants.update((name, (value, scale.TYPE)) for name, value in scale.TOKENS.items())
    for contract in tuple(catalog.contracts.values()):
        if not scale.available(contract.operation, catalog.language_version):
            continue
        overload_id = contract.overload_id + scale.SUFFIX
        variant = replace(contract, overload_id=overload_id, assertion_id=contract.assertion_id + scale.SUFFIX,
            signature_sha256=digest({'base': contract.signature_sha256, 'profile': 'openpine.chart-scale.1'}),
            parameter_ids=(*contract.parameter_ids, f'parameter.{overload_id}.scale'),
            parameter_types=(*contract.parameter_types, (scale.TYPE,)),
            qualifier_limits=(*contract.qualifier_limits, 'const'))
        catalog.contracts[overload_id] = variant
        catalog.scale_contracts[contract.overload_id] = variant


def extend(catalog, signature, contract):
    variant = catalog.scale_contracts.get(contract.overload_id)
    if variant is None:
        return signature, contract
    parameter = {'name': 'scale', 'parameter_id': variant.parameter_ids[-1],
                 'requiredness': {'state': 'required'}, 'default': {'kind': 'none'}}
    return {**signature, 'parameters': [*signature['parameters'], parameter]}, variant


def positional_count(version):
    return 5 if version < 4 else 6


def select(binder, call, signature, contract):
    if 'declaration' not in contract.effects:
        return signature, contract
    version = binder.catalog.language_version
    positional = (call.name in ('study', 'indicator') and
                  sum(arg.name is None for arg in call.arguments) == positional_count(version))
    if not positional and not any(arg.name == 'scale' for arg in call.arguments):
        return signature, contract
    if contract.overload_id not in binder.catalog.scale_contracts:
        binder.fail('LANGUAGE_INVALID', 'CHART_SCALE_VERSION',
                    'Chart scale is unavailable for this Pine signature.', call.span)
    return extend(binder.catalog, signature, contract)


def validate(binder, call, bound, positions):
    from .binder import BoundLiteral, BoundLoad

    option = bound[positions['scale']]
    source = call.arguments[option.source_index].expression
    value = option.expression
    if (not isinstance(source, parser.Reference) or source.name not in scale.TOKENS
            or not isinstance(value, BoundLiteral) or value.value != scale.TOKENS[source.name]):
        binder.fail('LANGUAGE_INVALID', 'CHART_SCALE_VALUE', 'Use scale.left, scale.right, or scale.none.', value.span)
    overlay = bound[positions['overlay']].expression
    if isinstance(overlay, BoundLoad):
        overlay = binder.shared.get('constant_bindings', {}).get(overlay.binding.binding_id, overlay)
    if isinstance(overlay, BoundLiteral):
        try:
            scale.validate(value.value, overlay.value)
        except ValueError as error:
            binder.fail('LANGUAGE_INVALID', 'CHART_SCALE_OVERLAY', str(error), value.span)
