"""Immutable, independently covered corporate-action streams (UTC milliseconds)."""
from bisect import bisect_right
from math import isfinite
import re

from .calendar import next_open
from .model import CallIR, Diagnostic, OpenPineError
from .wire import digest

FIELDS = {'dividends': ('gross', 'net'), 'splits': ('numerator', 'denominator')}
PARAMETERS = ('ticker', 'field', 'gaps', 'lookahead', 'ignore_invalid_symbol')
OPERATIONS = {'corporate.' + family for family in FIELDS}


def fail(code, message):
    raise OpenPineError(Diagnostic('INPUT_INVALID', code, message))


def timestamp(value):
    return type(value) is int and 0 <= value < 2**63


def normalize(value):
    if (type(value) is not dict or set(value) != {'schema_version', 'source', 'streams'}
            or value['schema_version'] != 'openpine.corporate-actions.1'
            or type(value['source']) is not str or not 0 < len(value['source']) <= 256
            or type(value['streams']) is not list or not 1 <= len(value['streams']) <= 256):
        fail('ACTION_SCHEMA', 'Invalid corporate-action dataset header.')
    streams = {}
    for stream in value['streams']:
        if type(stream) is not dict or set(stream) != {'symbol','family','start_ms','end_ms','currency','events'}:
            fail('ACTION_SCHEMA', 'Invalid corporate-action stream.')
        symbol, family = stream['symbol'], stream['family']
        if (type(symbol) is not str or len(symbol) > 256 or not re.fullmatch(r'[^:\s]+:[^:\s]+', symbol)
                or type(family) is not str or family not in FIELDS or (symbol, family) in streams):
            fail('ACTION_SCHEMA', 'Expected one stream per qualified symbol and family.')
        start, end = stream['start_ms'], stream['end_ms']
        currency = stream['currency']
        if (not timestamp(start) or not timestamp(end) or start >= end
                or (family == 'dividends' and (type(currency) is not str or not re.fullmatch('[A-Z]{3}',currency)))
                or (family == 'splits' and currency is not None) or type(stream['events']) is not list):
            fail('ACTION_SCHEMA', 'Invalid corporate-action coverage or currency.')
        events = {}
        for row in stream['events']:
            if type(row) is not dict or set(row) != {'effective_ms','available_ms',*FIELDS[family]}:
                fail('ACTION_SCHEMA', 'Invalid corporate-action event fields.')
            effective, available = row['effective_ms'], row['available_ms']
            if not timestamp(effective) or not timestamp(available) or not start <= max(effective, available) < end:
                fail('ACTION_SCHEMA', 'Event visibility is outside declared coverage.')
            item = dict(row)
            for field in FIELDS[family]:
                number = row[field]
                if family == 'dividends' and field == 'net' and number is None:
                    continue
                if (type(number) not in (int,float) or abs(number) > 1e308 or not isfinite(number)
                        or (family == 'splits' and (type(number) is not int or not 0 < number <= 2**53))):
                    fail('ACTION_VALUE', 'Expected a finite payout or positive integer split ratio.')
                item[field] = float(number) if family == 'dividends' else number
            if effective in events and events[effective] != item:
                fail('ACTION_CONFLICT', 'Conflicting records for one effective instant.')
            events[effective] = item
        streams[symbol, family] = {**stream, 'events':[events[t] for t in sorted(events)]}
    return {**value, 'streams':[streams[key] for key in sorted(streams)]}


class ActionIndex:
    """A shared immutable search index. The bar clock also serves as the restore cursor."""
    def __init__(self, value):
        snapshot = normalize(value)
        self.sha256 = digest(snapshot)
        self.streams = {}
        self.symbols = set()
        for stream in snapshot['streams']:
            self.symbols.add(stream['symbol'])
            rows = sorted(stream['events'], key=lambda r:(max(r['available_ms'],r['effective_ms']),r['effective_ms']))
            times = [max(r['available_ms'],r['effective_ms']) for r in rows]
            self.streams[stream['symbol'], stream['family']] = stream, times, rows

    def select(self, symbol, family, ignore, currency):
        if symbol not in self.symbols:
            if ignore:
                return None
            fail('ACTION_SYMBOL', 'Unknown symbol in corporate snapshot: ' + symbol)
        stream = self.streams.get((symbol, family))
        if stream is None:
            fail('ACTION_COVERAGE', 'Missing corporate-action family for ' + symbol)
        if currency and currency != stream[0]['currency']:
            fail('ACTION_CURRENCY', 'Currency conversion requires a pinned FX source.')
        return stream

class BoundActions:
    def __init__(self, index, timeframe, requirements, chart_currency=None):
        self.sha256 = index.sha256
        self.timeframe = timeframe
        self.sites = {}
        for req in requirements:
            currency = req['currency'] or (chart_currency if req['default_chart_currency'] else '')
            stream = index.select(req['symbol'],req['family'],req['ignore_invalid_symbol'],currency)
            self.sites[req['call_site_id']] = (stream,req['field'].split('.')[1],req['gaps'],req['lookahead'])

    def value(self, site, frame, previous):
        stream, name, gaps, lookahead = self.sites[site]
        timeframe = self.timeframe
        if stream is None:
            return None
        meta, times, rows = stream
        cutoff = frame.time if lookahead == 'on' else next_open(frame.time,timeframe) - 1
        if frame.observation_time_ms is not None:
            cutoff = min(cutoff, frame.observation_time_ms)
        if not meta['start_ms'] <= frame.time <= cutoff < meta['end_ms']:
            fail('ACTION_COVERAGE', 'Requested candle is outside attested corporate-action coverage.')
        pos = bisect_right(times,cutoff) - 1
        if pos < 0:
            return None
        if gaps == 'on':
            lower = (frame.time-1 if previous is None else
                     previous if lookahead == 'on' else next_open(previous,timeframe)-1)
            if times[pos] <= lower:
                return None
        value = rows[pos][name]
        return None if value is None else float(value)


def requests(program):
    from .context_ir import ir_nodes
    return [n for n in ir_nodes(program.statements) if isinstance(n,CallIR) and n.operation in OPERATIONS]


def requirements(program, settings, primary):
    from .primary_targets import resolve
    result = []
    for node in requests(program):
        symbol, _, _ = resolve(program,node.arguments[0].expression,'symbol',settings,primary=primary,fixed_ticker=True)
        params = [a.expression.value for a in node.arguments[1:]]
        field, gaps, lookahead, ignore, *currency = params
        family = node.operation.split('.')[1]
        if field not in [family+'.'+f for f in FIELDS[family]] or gaps not in ('on','off') or lookahead not in ('on','off'):
            fail('ACTION_ARGUMENT', 'Invalid corporate-action field or alignment.')
        result.append(dict(call_site_id=node.call_site_id, default_chart_currency=bool(currency and not currency[0]), symbol=symbol,family=family,field=field,gaps=gaps,lookahead=lookahead,
                           ignore_invalid_symbol=ignore,currency=currency[0] if currency else ''))
    return result


def validate_call(program, node):
    from .primary_targets import shape
    from .model import LiteralIR
    version = program.identity.language_version
    family = node.operation.split('.')[1]
    size = 6 if family == 'dividends' and version >= 5 else 5
    if version < 4 or family not in FIELDS or len(node.arguments) != size:
        fail('ACTION_ARGUMENT','Corporate-action signature differs from its version.')
    shape(program,node.arguments[0].expression)
    options = [a.expression for a in node.arguments[1:]]
    if any(not isinstance(expr,LiteralIR) for expr in options):
        fail('ACTION_ARGUMENT','Corporate-action options must be fixed literals.')
    field, gaps, lookahead, ignore, *currency = [expr.value for expr in options]
    if (field not in tuple(family+'.'+f for f in FIELDS[family]) or gaps not in ('off','on')
            or lookahead not in ('off','on') or type(ignore) is not bool
            or currency and (type(currency[0]) is not str or not re.fullmatch('[A-Z]{3}|',currency[0]))):
        fail('ACTION_ARGUMENT','Invalid corporate-action field, currency or alignment.')
