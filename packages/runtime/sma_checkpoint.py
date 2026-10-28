"""Lossless compact SMA checkpoint: exact prefix anchor and original doubles."""
import math
from .history import Ring

SCHEMA='openpine.sma-prefix.1'

def units(value):
    numerator,denominator=value.as_integer_ratio()
    return numerator << (1074-(denominator.bit_length()-1))

def export_prefixes(state):
    ring=state.prefixes
    retained=min(state.count,ring.capacity-1)
    anchor=ring.ago(retained)
    samples=[]
    for offset in range(retained-1,-1,-1):
        samples.append((ring.ago(offset)-ring.ago(offset+1))/(1<<1074))
    return dict(schema_version=SCHEMA,count=ring.count,capacity=ring.capacity,anchor=anchor,samples=samples)

def restore_prefixes(value,budget,capacity,count,total):
    if (type(value) is not dict or value.keys()!={'schema_version','count','capacity','anchor','samples'}
            or value['schema_version']!=SCHEMA or type(value['capacity']) is not int or value['capacity']!=capacity
            or type(value['count']) is not int or value['count']!=count+1
            or type(value['anchor']) is not int or value['anchor'].bit_length()>4096
            or type(value['samples']) is not list or len(value['samples'])!=min(count,capacity-1)):
        raise ValueError('Invalid compact SMA prefix checkpoint.')
    samples=value['samples'];before=count-len(samples);anchor=value['anchor']
    if (abs(anchor)>before*units(float.fromhex('0x1.fffffffffffffp+1023'))
            or any(type(v) is not float or not math.isfinite(v) for v in samples)):
        raise ValueError('Invalid SMA anchor or samples.')
    # Charge before allocating the exact integer ring. The caller publishes only
    # after all runtime state has passed validation.
    budget.reserve(len(samples)+1,'exact')
    prefixes=[anchor]
    for sample in samples:prefixes.append(prefixes[-1]+units(sample))
    if prefixes[-1]!=total:raise ValueError('SMA samples differ from exact total.')
    ring=Ring(budget,capacity,'exact');ring.count=count+1
    if before:
        # Physical slots wrap; the compact representation is chronological.
        ring.items=[0]*len(prefixes)
        for offset,prefix in enumerate(prefixes):ring.items[(before+offset)%capacity]=prefix
    else:ring.items=prefixes
    return ring
