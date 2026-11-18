"""Versioned ticker and prepared request contracts for historical HA candles."""
from dataclasses import replace

from .model import CallIR, Diagnostic, LoadIR, OpenPineError, StoreIR
from .wire import digest

OPERATION = 'context.heikin_ashi_ticker'
SUFFIX = '.heikinashi.primary.prepared'
CANDLE_TYPE = 'heikin_ashi'
TICKER_PREFIX = '@openpine/heikin-ashi '


def public_name(version):
    return 'ticker.heikinashi' if version >= 5 else 'heikinashi' if version >= 2 else None


def request_contract(base, version, ticker):
    from .primary_targets import contract
    primary = contract(base, version)
    return replace(primary, overload_id=base.overload_id + SUFFIX, assertion_id=base.assertion_id + SUFFIX,
                   signature_sha256=digest({'primary': primary.signature_sha256, 'ticker': ticker.signature_sha256,
                                            'candles': 'openpine.heikin-ashi.1'}))


def transforms(node):
    return node.overload_id.endswith(SUFFIX)


def symbol_operand(program, expression):
    """Unwrap one written HA ticker, possibly through immutable global aliases."""
    from .context_ir import ir_nodes

    def fail(message):
        raise OpenPineError(Diagnostic('PROFILE_UNCOVERED', 'HEIKIN_ASHI_TARGET', message,
                                      expression.span, language_version=program.identity.language_version))

    node = expression
    if isinstance(node, LoadIR):
        declarations = {n.binding.binding_id: n for n in program.statements if isinstance(n, StoreIR) and n.declaration}
        modified = {n.binding.binding_id for n in ir_nodes(program.statements) if isinstance(n, StoreIR) and not n.declaration}
        seen = set()
        while isinstance(node, LoadIR):
            key = node.binding_id
            declaration = declarations.get(key)
            if declaration is None or key in modified or key in seen or len(seen) == 16:
                fail('Heikin Ashi tickers require immutable, unconditional global aliases up to depth 16.')
            binding = declaration.binding
            if binding.scope_id != 'scope:root' or binding.namespace or binding.mode != 'bar' or binding.value_type != node.value_type:
                fail('Heikin Ashi tickers require immutable, unconditional global aliases.')
            seen.add(key)
            node = declaration.expression
    if not isinstance(node, CallIR) or node.operation != OPERATION or len(node.arguments) != 1:
        fail('Use a single Heikin Ashi ticker call in the request symbol.')
    return node.arguments[0].expression
