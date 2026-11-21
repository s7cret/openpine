"""Units of bounded expressions for explicit security currency conversion."""

from .model import BinaryIR, CallIR, ConvertIR, HistoryIR, LiteralIR, ReferenceIR, TupleIR, UnaryIR


def result_units(node):
    """Return one unit per result component; unknown units require a diagnostic."""
    if isinstance(node, CallIR) and node.operation == 'context.request':
        return result_units(node.arguments[2].expression)
    if isinstance(node, TupleIR):
        return tuple(_scalar_unit(element) for element in node.elements)
    return (_scalar_unit(node),)


def _scalar_unit(node):
    if node.value_type.base == 'bool' or isinstance(node, LiteralIR):
        return 'scalar'
    if isinstance(node, ReferenceIR):
        slot = node.contract.slot
        if slot in ('open', 'high', 'low', 'close', 'hl2'):
            return 'price'
        if slot == 'volume':
            return 'volume'
    if isinstance(node, (HistoryIR, ConvertIR, UnaryIR)):
        return _scalar_unit(node.operand)
    if isinstance(node, CallIR) and node.operation in ('series.ema', 'series.ema.sma_seed', 'series.sma'):
        return _scalar_unit(node.arguments[0].expression)
    if isinstance(node, BinaryIR):
        left, right = _scalar_unit(node.left), _scalar_unit(node.right)
        if node.operator in ('+', '-') and left == right:
            return left
        if node.operator == '*' and left == 'scalar':
            return right
        if node.operator in ('*', '/') and right == 'scalar':
            return left
        if node.operator == '/' and left == right and left != 'unknown':
            return 'scalar'
    return 'unknown'

def target_currency(node):
    if 'fx' not in node.overload_id.split('.'):
        return ''
    return node.arguments[4 if node.operation == 'context.lower_tf' else 6].expression.value


def instrument_currency(manifest):
    return manifest.get("provenance",{}).get("environment",{}).get("instrument",{}).get("currency")


def conversion_inputs(nodes, market_currencies, chart_currency):
    """Resolve each price's denomination and caller currency, from leaves to chart."""
    from .corporate import fail
    callers = {}
    for node in nodes:
        child = node.arguments[2].expression
        if isinstance(child,CallIR) and child.operation == 'context.request':
            parent_currency = market_currencies.get(node.call_site_id)
            if (target_currency(child) == '@chart' and child.call_site_id in callers
                    and callers[child.call_site_id] != parent_currency):
                fail('FX_CURRENCY','The nested call requires a separate conversion for each enclosing currency.')
            callers[child.call_site_id] = parent_currency
    outputs, inputs = {}, {}
    for node in nodes:
        site = node.call_site_id
        caller = callers.get(site, chart_currency)
        child = node.arguments[2].expression
        source = (outputs[child.call_site_id] if isinstance(child,CallIR) and child.operation == 'context.request'
                  else market_currencies.get(site))
        target = target_currency(node)
        outputs[site] = caller if target == '@chart' else target or source
        inputs[site] = source,caller
    return inputs


def pin_currency(site,node,source,chart,rates):
    from dataclasses import replace
    from .corporate import fail
    name=target_currency(node)
    if not name:
        return site
    target=chart if name=="@chart" else name
    if not target:
        fail("FX_CURRENCY","Chart currency requires pinned instrument metadata.")
    if site.resolution=="unknown_symbol":
        return replace(site,target_currency=target)
    conversion=bind_conversion(node,source,chart,rates)
    return replace(site,source_currency=source,target_currency=target,
                   fx_snapshot_id=rates.sha256 if conversion else None)


def bind_conversion(node, source_currency, chart_currency, rates):
    from math import isfinite
    from .corporate import fail

    target = target_currency(node)
    units = result_units(node.arguments[2].expression)
    if not target or 'price' not in units:
        return None
    quote = chart_currency if target == '@chart' else target
    if not source_currency or not quote:
        fail('FX_CURRENCY', 'Security conversion requires pinned instrument currencies.')
    if source_currency == quote:
        return None
    if rates is None:
        fail('FX_SNAPSHOT', 'Security conversion requires a pinned FX snapshot.')
    rate_at = rates.bind(source_currency, quote)

    def convert(value, at):
        if value is None:
            return None
        components = value if isinstance(value, tuple) else (value,)
        if not any(unit == 'price' and item is not None for item,unit in zip(components,units,strict=True)):
            return value
        rate = rate_at(at)
        result = tuple(item * rate if item is not None and unit == 'price' else item
                       for item,unit in zip(components,units,strict=True))
        if any(unit == 'price' and item is not None and not isfinite(item) for item,unit in zip(result,units,strict=True)):
            fail('FX_VALUE', 'Converted security price exceeds the supported numeric range.')
        return result if isinstance(value, tuple) else result[0]
    return convert
