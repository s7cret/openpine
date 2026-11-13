"""Closed ABI for volume series and the versioned VWAP signatures."""

from .technical import validate_market_arguments

AD = 'volume.accdist'
OBV = 'volume.obv'
PVT = 'volume.pvt'
MFI = 'volume.mfi'
DAILY = 'volume.vwap_daily'
TYPICAL = 'volume.vwap_typical'
ANCHOR = 'volume.vwap_anchor'
BANDS = 'volume.vwap_bands'
CUMULATIVE = frozenset((AD, OBV, PVT))
VWAP = frozenset((DAILY, TYPICAL, ANCHOR, BANDS))
OPERATIONS = CUMULATIVE | VWAP | {MFI}
EFFECTS = ('reads_history', 'updates_state')
MARKET = {
    AD: ('high', 'low', 'close', 'volume'),
    OBV: ('close', 'previous_close', 'volume'),
    PVT: ('close', 'previous_close', 'volume'),
    MFI: ('volume',),
    DAILY: ('time', 'volume'),
    TYPICAL: ('time', 'volume', 'high', 'low', 'close'),
    ANCHOR: ('volume',),
    BANDS: ('volume',),
}
LENGTH_INDEX = {MFI: len(MARKET[MFI]) + 1}
ANCHOR_INDEX = len(MARKET[ANCHOR]) + 1
NUMERIC = ('float', 'int')


def parameters(operation, version):
    if operation == MFI:
        return (('series', NUMERIC), ('length', ('int',)))
    if operation == DAILY:
        return (('source' if version >= 5 else 'x', NUMERIC),)
    if operation in (ANCHOR, BANDS):
        args = (('source', NUMERIC), ('anchor', ('bool',)))
        return (*args, ('stdev_mult', NUMERIC)) if operation == BANDS else args
    return ()


def apis(version):
    yield 'accdist', AD, True
    yield 'vwap', TYPICAL, True
    if version >= 4:
        yield 'obv', OBV, True
        yield 'pvt', PVT, True
        yield 'mfi', MFI, False
    if version <= 5:
        yield 'vwap', DAILY, False
    if version >= 5:
        yield 'vwap', ANCHOR, False
        yield 'vwap', BANDS, False


def validate_call(node):
    slots = MARKET[node.operation]
    validate_market_arguments(node.arguments, slots)
    if any(arg.origin != 'source' for arg in node.arguments[len(slots):]):
        raise ValueError('Volume indicator parameters must come from the source.')
