"""Numeric array statistics with exact binary sums before final rounding."""
from collections import Counter
from fractions import Fraction
import math


def checked_integer(value,heap,node):
    if value>2**63-1:heap.error('ARRAY_NUMERIC','Array statistic exceeds signed int64.',node)
    return value


def sqrt_ratio(numerator, denominator):
    if not numerator:return 0.0
    # Scale by an even power of two so the quotient fits before taking its root.
    exponent=(numerator.bit_length()-denominator.bit_length())//2
    if exponent>=0:
        scaled=numerator/(denominator << (2*exponent))
    else:
        scaled=(numerator << (-2*exponent))/denominator
    return math.ldexp(math.sqrt(scaled),exponent)


def evaluate(heap,evaluation,node,items,biased):
    op=node.operation
    size=len(items)
    if op=='standardize':
        heap.capacity(len(heap.objects)+1,heap.cells+size,size,heap.bytes,node)
    heap.charge(size,evaluation,node)
    if biased is None:return None
    if op=='range':
        bottom=top=None
        for x in items:
            if x is not None:
                bottom=x if bottom is None else min(bottom,x)
                top=x if top is None else max(top,x)
        if bottom is None:return None
        value=top-bottom
        if node.element=='float' and not math.isfinite(value):
            heap.error('ARRAY_NUMERIC','Array range exceeds the numeric range.',node)
        return checked_integer(value,heap,node) if node.element=='int' else value

    # All finite int/float values have a power-of-two denominator. Rescale
    # the two sums exactly when a smaller binary unit is encountered.
    scale=1
    count=total=squares=0
    for x in items:
        if x is None:continue
        n,d=x.as_integer_ratio()
        if d>scale:
            factor=d//scale
            total*=factor
            squares*=factor*factor
            scale=d
        else:n*=scale//d
        count+=1
        total+=n
        squares+=n*n
    scatter=count*squares-total*total
    integer=node.element=='int'
    if op=='standardize':
        heap.charge(size,evaluation,node)
        normalized=[]
        for x in items:
            if x is None or not scatter:
                normalized.append(None)
                continue
            n,d=x.as_integer_ratio()
            centered=count*n*(scale//d)-total
            square=centered*centered
            magnitude=math.isqrt(square//scatter) if integer else sqrt_ratio(square,scatter)
            normalized.append(-magnitude if centered<0 else magnitude)
        return heap.create(node.element,normalized,0,evaluation,node)
    divisor=count*(count if biased else count-1)*scale*scale
    if not divisor:return None
    try:
        if integer:
            whole=scatter//divisor
            return checked_integer(whole if op=='variance' else math.isqrt(whole),heap,node)
        return scatter/divisor if op=='variance' else sqrt_ratio(scatter,divisor)
    except OverflowError:
        heap.error('ARRAY_NUMERIC','Array statistic exceeds the numeric range.',node)


def ranked(heap, evaluation, node, obj, args):
    items = obj.items
    if not len(items):
        return None
    operation = node.operation
    heap.charge(len(items), evaluation, node)
    if operation == 'percentrank':
        target = items[heap.index(args[1], obj, node)]
        if target is None:
            return None
        count = at_or_below = 0
        for value in items:
            if value is not None:
                count += 1
                at_or_below += value <= target
        rank = Fraction(100 * at_or_below, count)
        return int(rank) if node.element == 'int' else float(rank)

    values = [value for value in items if value is not None]
    if not values:
        return None
    if operation == 'mode':
        frequencies = Counter(values)
        highest = max(frequencies.values())
        return min(value for value, count in frequencies.items() if count == highest)

    percentage = 50 if operation == 'median' else args[1]
    if percentage is None:
        return None
    if not 0 <= percentage <= 100:
        heap.error('ARRAY_PERCENTAGE', 'Percentage must be between 0 and 100.', node)
    heap.charge(len(values) * (len(values) - 1).bit_length(), evaluation, node)
    values.sort()
    fraction = Fraction(percentage) / 100
    if operation == 'percentile_nearest_rank':
        return values[max(0, math.ceil(len(values) * fraction) - 1)]
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    weight = position - lower
    if not weight:
        return values[lower]
    # Exact convex combination avoids int64 precision loss and float overflow.
    result = (1 - weight) * Fraction(values[lower]) + weight * Fraction(values[lower + 1])
    return int(result) if node.element == 'int' else float(result)
