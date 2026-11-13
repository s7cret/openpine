"""Call shapes and checked implicit inputs for volatility channels."""

BB = 'channel.bb'
BBW = 'channel.bbw'
KC = 'channel.kc'
KCW = 'channel.kcw'
BOLLINGER = frozenset((BB, BBW))
KELTNER = frozenset((KC, KCW))
OPERATIONS = BOLLINGER | KELTNER
MARKET = ('high', 'low', 'previous_close')
EFFECTS = ('reads_history', 'updates_state')


def validate_call(node):
    from .model import LiteralIR
    from .technical import validate_market_arguments

    if node.operation in KELTNER:
        validate_market_arguments(node.arguments, MARKET)
        public = node.arguments[len(MARKET):]
        if len(public) == 4 and public[3].origin == 'default':
            flag = public[3].expression
            if not isinstance(flag, LiteralIR) or flag.value is not True:
                raise ValueError('Keltner range defaults to true.')
        required = public[:3] if not node.overload_id.endswith('.explicit') else public
    else:
        required = node.arguments
        if node.operation == BB and required[1].expression.value_type.qualifier != 'series':
            raise ValueError('Dynamic Bollinger Bands require series-qualified length.')
    if any(argument.origin != 'source' for argument in required):
        raise ValueError('Required channel arguments must come from the source.')
