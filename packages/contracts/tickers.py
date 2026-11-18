"""Fixed market identifiers constructed by Pine programs."""
from .heikin_ashi import OPERATION as HA, TICKER_PREFIX

NEW = 'ticker.new'
STANDARD = 'ticker.standard'
MODIFY = 'ticker.modify'
INHERIT = 'ticker.inherit'
SUFFIX = '.fixed-ticker.primary.prepared'
PARAMETERS = {NEW: ('prefix', 'ticker'), STANDARD: ('symbol',), MODIFY: ('tickerid',),
              INHERIT: ('from_tickerid', 'symbol')}


def names(version):
    if version >= 5:
        return {name: name for name in PARAMETERS}
    return {'tickerid': NEW} if version >= 2 else {}


def invoke(operation, arguments):
    if any(type(value) is not str for value in arguments):
        raise ValueError('Ticker components must be defined strings.')
    if operation == NEW:
        if any(not value or ':' in value or any(ch.isspace() for ch in value) for value in arguments):
            raise ValueError('Expected a nonempty exchange prefix and instrument name.')
        result = ':'.join(arguments)
    elif operation == MODIFY:
        return arguments[0]
    elif operation == INHERIT:
        template, target = arguments
        if target.startswith(TICKER_PREFIX):
            raise ValueError('Inheritance requires an ordinary destination symbol.')
        return invoke(HA, (target,)) if template.startswith(TICKER_PREFIX) else target
    elif operation == STANDARD:
        result = arguments[0].removeprefix(TICKER_PREFIX)
    elif operation == HA:
        if arguments[0].startswith(TICKER_PREFIX) or len(arguments[0]) > 256:
            raise ValueError('Expected one underlying market symbol.')
        return TICKER_PREFIX + arguments[0]
    else:
        raise ValueError('Unknown ticker constructor.')
    if len(result) > 256:
        raise ValueError('Market identifier exceeds 256 characters.')
    return result
