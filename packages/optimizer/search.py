"""Finite, versioned search independent of process scheduling and completion order."""
from decimal import localcontext
import hashlib
import itertools
import math

from packages.contracts.inputs import definitions, settings_for
from packages.contracts.optimization import ALGORITHM, MAX_CANDIDATES, decimal_value
from packages.contracts.wire import digest,canonical


def candidates(program, space, *, method, budget, seed, inputs=None):
    if method not in ('grid', 'random') or type(budget) is not int or not 1 <= budget <= MAX_CANDIDATES:
        raise ValueError('Use grid/random and a candidate budget in 1..256.')
    if type(seed) is not int or not 0 <= seed <= 2**32-1:
        raise ValueError('Seed must be an integer in 0..4294967295.')
    if type(space) is not dict or not 1 <= len(space) <= 64:
        raise ValueError('Search space requires 1..64 named finite axes.')
    if len(canonical(space))>1024*1024:raise ValueError('Search space exceeds 1 MiB.')
    rows = definitions(program); known = {d.input_id: d for d in rows}; aliases = {d.name: d.input_id for d in rows}
    base = settings_for(program, inputs); axes = {}
    for key, axis in space.items():
        identifier = key if key in known else aliases.get(key)
        if identifier is None or identifier in axes:
            raise ValueError('Unknown or duplicate search axis: ' + str(key))
        definition = known[identifier]
        if not definition.active:
            raise ValueError('Inactive inputs cannot be searched.')
        if type(axis) is dict and axis.keys() == {'min', 'max', 'step'} and definition.input_type in ('int', 'float'):
            with localcontext() as context:
                context.prec = 700
                lo, hi, step = (decimal_value(axis[k]) for k in ('min', 'max', 'step'))
                if step <= 0 or hi < lo:
                    raise ValueError('Search range requires min <= max and step > 0.')
                if definition.input_type == 'int' and any(v != v.to_integral_value() for v in (lo, hi, step)):
                    raise ValueError('Integer range bounds and step must be integral.')
                count = int((hi-lo)//step)+1
                if not 1 <= count <= 10000:
                    raise ValueError('An axis may contain at most 10000 values.')
                values = [int(lo+i*step) if definition.input_type == 'int' else float(lo+i*step) for i in range(count)]
        elif type(axis) is list and 1 <= len(axis) <= 10000:
            values = axis
        else:
            raise ValueError('Axis requires a finite value list or explicit min/max/step.')
        normalized = []
        for value in values:
            normalized.append(settings_for(program, {**base['values'], identifier: value})['values'][identifier])
        if len({digest(v) for v in normalized}) != len(normalized):
            raise ValueError('Search axis normalizes to duplicate values.')
        axes[identifier] = normalized
        if sum(len(v) for v in axes.values())>16384:raise ValueError('Combined axes exceed 16384 values.')
    keys = sorted(axes); cardinality = math.prod(len(axes[k]) for k in keys)
    if cardinality > 2**63-1:
        raise ValueError('Search space exceeds the admitted finite cardinality.')
    if method == 'grid':
        if cardinality > budget:
            raise ValueError('Full grid exceeds the candidate budget; use random or a smaller grid.')
        indices = list(range(cardinality))
    else:
        if budget > cardinality:
            raise ValueError('Random budget exceeds unique search combinations.')
        # Floyd sampling: fixed O(budget) memory, no retries near full coverage.
        selected = set(); indices = []; counter = 0
        for upper in range(cardinality-budget, cardinality):
            bound = upper+1; ceiling = 2**256-(2**256 % bound)
            while True:
                raw = int.from_bytes(hashlib.sha256(f'{ALGORITHM}:{seed}:{counter}'.encode()).digest(), 'big'); counter += 1
                if raw < ceiling:
                    draw = raw % bound; break
            chosen = upper if draw in selected else draw
            selected.add(chosen); indices.append(chosen)
    variants = [];settings_bytes=0
    for index in indices:
        values = dict(base['values']); cursor = index
        for key in reversed(keys):
            cursor, slot = divmod(cursor, len(axes[key])); values[key] = axes[key][slot]
        settings=settings_for(program, values);settings_bytes+=len(canonical(settings))
        if settings_bytes>2*1024*1024:raise ValueError('Frozen candidate settings exceed the 2 MiB preparation budget.')
        variants.append(settings)
    return {'algorithm': ALGORITHM, 'method': method, 'seed': seed, 'budget': budget,
            'cardinality': cardinality, 'axes': axes, 'base_settings': base, 'candidates': variants}
