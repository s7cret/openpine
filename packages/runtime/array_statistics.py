"""Numeric array statistics with exact binary sums before final rounding."""
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
