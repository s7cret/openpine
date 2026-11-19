"""Immutable, independently covered corporate-action streams (UTC milliseconds)."""
from bisect import bisect_right
from math import isfinite
import re

from . import economic, financial
from .calendar import next_open
from .model import CallIR, Diagnostic, OpenPineError
from .wire import digest

FIELDS = {'dividends': ('gross', 'net'), 'splits': ('numerator', 'denominator'),
          'earnings': ('actual', 'estimate', 'standardized')}
PARAMETERS = ('ticker', 'field', 'gaps', 'lookahead', 'ignore_invalid_symbol')
OPERATIONS = {'corporate.' + family for family in FIELDS} | {'corporate.financial', 'corporate.economic'}


def fail(code, message):
    raise OpenPineError(Diagnostic('INPUT_INVALID', code, message))


def timestamp(value):
    return type(value) is int and 0 <= value < 2**63


def field_names(family, version):
    if family == 'earnings' and version == 4:
        return ('actual', 'estimate')
    return FIELDS[family]


def earnings_events(rows, start, end):
    publications = {}
    for row in rows:
        if type(row) is not dict or set(row) != {'period_end_ms', 'available_ms', 'field', 'value'}:
            fail('ACTION_SCHEMA', 'Expected a dated publication of one EPS field.')
        period, available, field = row['period_end_ms'], row['available_ms'], row['field']
        if (not timestamp(period) or not timestamp(available) or not start <= available < end
                or field not in FIELDS['earnings']):
            fail('ACTION_SCHEMA', 'Invalid earnings period, publication time or field.')
        number = row['value']
        if number is not None and (type(number) not in (int, float) or abs(number) > 1e308 or not isfinite(number)):
            fail('ACTION_VALUE', 'Expected finite EPS or null.')
        key = (available, period, field)
        item = {**row, 'value': None if number is None else float(number)}
        if key in publications and publications[key] != item:
            fail('ACTION_CONFLICT', 'Conflicting EPS for the same field, publication and period.')
        publications[key] = item
    return [publications[key] for key in sorted(publications)]


def normalize(value):
    if (type(value) is not dict or set(value) != {'schema_version', 'source', 'streams'}
            or value['schema_version'] != 'openpine.corporate-actions.1'
            or type(value['source']) is not str or not 0 < len(value['source']) <= 256
            or type(value['streams']) is not list or not 1 <= len(value['streams']) <= 256):
        fail('ACTION_SCHEMA', 'Invalid corporate-action dataset header.')
    streams = {}
    for stream in value['streams']:
        if isinstance(stream,dict) and stream.get('family') == 'economic':
            normalized = economic.normalize(stream)
            key = economic.key(normalized)
            if key in streams:
                fail('ECONOMIC_SCHEMA','Duplicate economic stream.')
            streams[key] = normalized
            continue
        keys = {'symbol','family','start_ms','end_ms','currency','events'}
        if isinstance(stream, dict) and stream.get('family') == 'financial':
            keys.update(('financial_id', 'period', 'unit'))
        if type(stream) is not dict or set(stream) != keys:
            fail('ACTION_SCHEMA', 'Invalid corporate-action stream.')
        symbol, family = stream['symbol'], stream['family']
        if (type(symbol) is not str or len(symbol) > 256 or not re.fullmatch(r'[^:\s]+:[^:\s]+', symbol)
                or type(family) is not str or family not in (*FIELDS, 'financial')):
            fail('ACTION_SCHEMA', 'Expected one stream per qualified symbol and family.')
        start, end = stream['start_ms'], stream['end_ms']
        currency = stream['currency']
        monetary = family != 'splits' and not (family == 'financial' and stream['unit'] == 'shares')
        if (not timestamp(start) or not timestamp(end) or start >= end
                or (monetary and (type(currency) is not str or not re.fullmatch('[A-Z]{3,4}',currency)))
                or (not monetary and currency is not None) or type(stream['events']) is not list):
            fail('ACTION_SCHEMA', 'Invalid corporate-action coverage or currency.')
        if family == 'financial':
            stream = {**stream, 'events': financial.normalize_events(stream)}
        key = financial.stream_key(stream)
        if key in streams:
            fail('ACTION_SCHEMA', 'Duplicate corporate-action stream.')
        if family == 'financial':
            streams[key] = stream
            continue
        if family == 'earnings':
            streams[symbol, family] = {**stream, 'events': earnings_events(stream['events'], start, end)}
            continue
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
        self.earnings_fields = {}
        self.replay_reports = {}
        self.symbols = set()
        for stream in snapshot['streams']:
            if stream['family'] == 'economic':
                self.streams[economic.key(stream)] = economic.timeline(stream)
                continue
            self.symbols.add(stream['symbol'])
            if stream['family'] == 'financial':
                key = financial.stream_key(stream)
                self.streams[key], self.replay_reports[key] = financial.timelines(stream)
                continue
            if stream['family'] == 'earnings':
                self.streams[stream['symbol'], 'earnings'] = stream, (), ()
                for field in FIELDS['earnings']:
                    rows = [row for row in stream['events'] if row['field'] == field]
                    self.earnings_fields[stream['symbol'], field] = stream, [r['available_ms'] for r in rows], rows
                continue
            rows = sorted(stream['events'], key=lambda r:(max(r['available_ms'],r['effective_ms']),r['effective_ms']))
            times = [max(r['available_ms'],r['effective_ms']) for r in rows]
            self.streams[stream['symbol'], stream['family']] = stream, times, rows

    def select_request(self, req, currency, rates=None, *, replay=False):
        if req['family'] == 'economic':
            pair = (req['country_code'], req['field'])
            if pair not in economic.UNITS:
                if req['ignore_invalid_symbol']:
                    return None
                fail('ECONOMIC_PAIR', 'Unknown economic country/field pair.')
            stream = self.streams.get(economic.key(req))
            if stream is None:
                fail('ECONOMIC_COVERAGE','Missing economic series in the selected snapshot.')
            return stream
        return self.select(req['symbol'], req['family'], req['ignore_invalid_symbol'], currency, rates,
                           key=financial.stream_key(req), replay=replay)

    def select(self, symbol, family, ignore, currency, rates=None, *, key=None, replay=False):
        if symbol not in self.symbols:
            if ignore:
                return None
            fail('ACTION_SYMBOL', 'Unknown symbol in corporate snapshot: ' + symbol)
        indices = self.replay_reports if replay and family == 'financial' else self.streams
        stream = indices.get(key or (symbol, family))
        if stream is None:
            fail('ACTION_COVERAGE', 'Missing corporate-action family for ' + symbol)
        if currency and stream[0]['currency'] is not None and currency != stream[0]['currency']:
            if rates is not None:
                rates.bind(stream[0]['currency'],currency)
                return stream
            fail('ACTION_CURRENCY', 'Currency conversion requires a pinned FX source.')
        return stream

class BoundActions:
    def __init__(self, index, timeframe, requirements, chart_currency=None, rates=None, rate_requests=(), *, replay=False):
        self.sha256 = index.sha256 if index is not None else None
        self.fx_sha256 = rates.sha256 if rates is not None else None
        self.rates = {r['call_site_id']:rates.bind(r['base'],r['quote'],r['ignore_invalid_currency']) for r in rate_requests}
        self.timeframe = timeframe
        self.sites = {}
        for req in requirements:
            currency = req['currency'] or (chart_currency if req['default_chart_currency'] else '')
            stream = index.select_request(req, currency, rates, replay=replay)
            field = 'value' if req['family'] in ('economic','financial') else req['field'].split('.')[1]
            if stream is not None and req['family'] == 'earnings':
                stream = index.earnings_fields[req['symbol'], field]
                field = 'value'
            convert = None
            if stream and currency and stream[0].get('currency') and currency != stream[0]['currency']:
                convert = rates.bind(stream[0]['currency'], currency)
            self.sites[req['call_site_id']] = stream, field, req['gaps'], req['lookahead'], convert

    def value(self, site, frame, previous):
        if site in self.rates:
            cutoff = next_open(frame.time,self.timeframe)-1
            if frame.observation_time_ms is not None:
                cutoff = min(cutoff, frame.observation_time_ms)
            return self.rates[site](cutoff)
        stream, name, gaps, lookahead, convert = self.sites[site]
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
        if value is not None and convert is not None:
            rate = convert(times[pos] if meta['family'] == 'financial' else rows[pos]['available_ms'])
            if rate is None:
                return None
            value *= rate
            if not isfinite(value):
                fail('FX_VALUE','Converted value exceeds the supported numeric range.')
        return None if value is None else float(value)


def requests(program, *, rates=False):
    from .context_ir import ir_nodes
    operations = {'corporate.currency_rate'} if rates else OPERATIONS
    return [n for n in ir_nodes(program.statements) if isinstance(n,CallIR) and n.operation in operations]


def requirements(program, settings, primary):
    from .primary_targets import resolve
    result = []
    for node in requests(program):
        if node.operation == 'corporate.economic':
            country, field, gaps, ignore = (a.expression.value for a in node.arguments)
            result.append(dict(call_site_id=node.call_site_id, family='economic',country_code=country,
                               field=field,gaps=gaps,ignore_invalid_symbol=ignore,
                               lookahead='off',currency='',default_chart_currency=False))
            continue
        symbol, _, _ = resolve(program,node.arguments[0].expression,'symbol',settings,primary=primary,fixed_ticker=True)
        params = [a.expression.value for a in node.arguments[1:]]
        family = node.operation.split('.')[1]
        if family == 'financial':
            metric, period, gaps, ignore, *currency = params
            result.append(dict(call_site_id=node.call_site_id, family=family, financial_id=metric,
                               period=period, symbol=symbol, gaps=gaps, lookahead='off',
                               default_chart_currency=bool(currency and not currency[0]),
                               ignore_invalid_symbol=ignore, currency=currency[0] if currency else ''))
            continue
        field, gaps, lookahead, ignore, *currency = params
        if field not in [family+'.'+f for f in FIELDS[family]] or gaps not in ('on','off') or lookahead not in ('on','off'):
            fail('ACTION_ARGUMENT', 'Invalid corporate-action field or alignment.')
        result.append(dict(call_site_id=node.call_site_id, default_chart_currency=bool(currency and not currency[0]), symbol=symbol,family=family,field=field,gaps=gaps,lookahead=lookahead,
                           ignore_invalid_symbol=ignore,currency=currency[0] if currency else ''))
    return result


def rate_requirements(program):
    result = []
    for node in requests(program, rates=True):
        a,b,ignore = (arg.expression.value for arg in node.arguments)
        result.append(dict(call_site_id=node.call_site_id, base=a, quote=b, ignore_invalid_currency=ignore))
    return result


def validate_call(program, node):
    from .primary_targets import shape
    from .model import LiteralIR
    version = program.identity.language_version
    family = node.operation.split('.')[1]
    if family == 'economic':
        if (version < 5 or len(node.arguments) != 4
                or any(not isinstance(a.expression,LiteralIR) for a in node.arguments)):
            fail('ECONOMIC_ARGUMENT','Expected fixed economic request arguments.')
        country,field,gaps,ignore = (a.expression.value for a in node.arguments)
        if type(country) is not str or type(field) is not str or gaps not in ('on','off') or type(ignore) is not bool:
            fail('ECONOMIC_ARGUMENT','Invalid economic request options.')
        return
    if family == 'currency_rate':
        if (version < 5 or len(node.arguments) != 3 or
                any(not isinstance(a.expression, LiteralIR) for a in node.arguments)):
            fail('FX_ARGUMENT','Expected two fixed currencies and a boolean.')
        a,b,ignore = (arg.expression.value for arg in node.arguments)
        if type(a) is not str or type(b) is not str or type(ignore) is not bool:
            fail('FX_ARGUMENT','Invalid FX arguments.')
        return
    size = 6 if family != 'splits' and version >= 5 else 5
    if version < 4 or family not in (*FIELDS, 'financial') or len(node.arguments) != size:
        fail('ACTION_ARGUMENT','Corporate-action signature differs from its version.')
    shape(program,node.arguments[0].expression)
    options = [a.expression for a in node.arguments[1:]]
    if any(not isinstance(expr,LiteralIR) for expr in options):
        fail('ACTION_ARGUMENT','Corporate-action options must be fixed literals.')
    if family == 'financial':
        metric, period, gaps, ignore, *currency = [expr.value for expr in options]
        if (not financial.supported(metric, period, version) or gaps not in ('off','on') or type(ignore) is not bool
                or currency and (type(currency[0]) is not str or not re.fullmatch('[A-Z]{3,4}|', currency[0]))):
            fail('ACTION_ARGUMENT', 'Invalid financial report options.')
        return
    field, gaps, lookahead, ignore, *currency = [expr.value for expr in options]
    if (field not in tuple(family+'.'+f for f in field_names(family, version)) or gaps not in ('off','on')
            or lookahead not in ('off','on') or type(ignore) is not bool
            or currency and (type(currency[0]) is not str or not re.fullmatch('[A-Z]{3,4}|',currency[0]))):
        fail('ACTION_ARGUMENT','Invalid corporate-action field, currency or alignment.')
