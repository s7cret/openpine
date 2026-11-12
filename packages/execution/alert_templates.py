"""Resolve indicator alert templates against one completed calculation pass."""
import re
from itertools import takewhile

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.alerts import CONDITION, MAX_MESSAGE_BYTES
from packages.contracts.calendar import utc_datetime
from packages.contracts.markers import MARKER_KINDS
from packages.contracts.model import CallIR, StoreIR
from packages.contracts.visual_ohlc import OPERATIONS as OHLC_OPERATIONS


_TOKEN = re.compile(r'\{\{(plot\("([^"\r\n]*)"\)|[^{}\r\n]+)\}\}')
_PLOTS = ('visual.plot', 'colors.plot', 'bands.plot')
_INDEXED_PLOTS = (*_PLOTS, *OHLC_OPERATIONS, *(f'markers.{name}' for name in MARKER_KINDS))
_CANDLE_FIELDS = ('open', 'high', 'low', 'close', 'volume')
_FIELDS = (*_CANDLE_FIELDS, 'time', 'timenow', 'exchange', 'ticker', 'interval')


def _number(value):
    if value is None:
        return 'na'
    return '0' if value == 0 else str(value).removesuffix('.0')


def _utc(timestamp):
    return utc_datetime(timestamp).isoformat(timespec='seconds').replace('+00:00', 'Z')


class ConditionTemplates:
    def __init__(self, program, context):
        self.version = program.identity.language_version
        exchange, ticker = context.symbol.split(':', 1)
        self.context = {'exchange': exchange, 'ticker': ticker, 'interval': context.timeframe}
        calls = [statement.expression if isinstance(statement, StoreIR) else statement
                 for statement in program.statements]
        outputs = [node for node in calls if isinstance(node, CallIR) and node.operation in _INDEXED_PLOTS]
        self.plot_sites = {node.call_site_id for node in outputs if node.operation in _PLOTS}
        indexed_sites = tuple(node.call_site_id for node in
                              takewhile(lambda node: node.operation in _PLOTS, outputs[:20]))
        self.templates = {}
        for node in calls:
            if isinstance(node, CallIR) and node.operation == CONDITION:
                pieces = self._parse(node, indexed_sites)
                if pieces:
                    self.templates[node.call_site_id] = (node, pieces)

    def _fail(self, node, code, message, *, category='RUNTIME_ERROR'):
        raise OpenPineError(Diagnostic(category, code, message, node.span, language_version=self.version))

    def _parse(self, node, indexed_sites):
        text = node.arguments[2].expression.value
        pieces, end = [], 0
        for match in _TOKEN.finditer(text):
            token, title = match.groups()
            suffix = token.removeprefix('plot_')
            if title is not None:
                piece = ('title', title)
            elif token in _FIELDS:
                piece = ('field', token)
            elif token.startswith('plot_') and suffix.isascii() and suffix.isdecimal():
                index = int(suffix) if len(suffix) <= 2 else 20
                if index >= len(indexed_sites) or suffix != str(index):
                    self._fail(node, 'ALERT_TEMPLATE_PLOT', f'Plot index {token!r} does not identify a supported plot().',
                               category='PROFILE_UNCOVERED')
                piece = ('plot', indexed_sites[index])
            else:
                continue
            pieces.extend((('text', text[end:match.start()]), piece))
            end = match.end()
        if pieces:
            pieces.append(('text', text[end:]))
        return pieces

    def render(self, event, intents, outputs):
        if not any(site in self.templates for site, _, _ in intents):
            return intents
        by_title, by_site = {}, {}
        for site, title, _, value, _ in outputs:
            if site in self.plot_sites:
                by_site[site] = value
                by_title.setdefault(title, []).append(value)
        fields = dict(self.context)
        result = []
        for site, message, frequency in intents:
            template = self.templates.get(site)
            if template is not None:
                node, pieces = template
                message = self._render_one(node, pieces, event, fields, by_title, by_site)
            result.append((site, message, frequency))
        return result

    def _render_one(self, node, pieces, event, fields, by_title, by_site):
        parts, remaining = [], MAX_MESSAGE_BYTES
        for kind, key in pieces:
            if kind == 'field':
                if key not in fields:
                    try:
                        fields[key] = (_number(getattr(event, key)) if key in _CANDLE_FIELDS
                                       else _utc(event.time if key == 'time' else event.event_time))
                    except ValueError as error:
                        self._fail(node, 'ALERT_TEMPLATE_TIME', str(error))
                part = fields[key]
            elif kind == 'title':
                values = by_title.get(key, ())
                if len(values) != 1:
                    self._fail(node, 'ALERT_TEMPLATE_PLOT', f'Plot title {key!r} does not identify a unique plot().')
                part = _number(values[0])
            elif kind == 'plot':
                if key not in by_site:
                    self._fail(node, 'ALERT_TEMPLATE_PLOT', 'Indexed plot() did not produce a value in this calculation.')
                part = _number(by_site[key])
            else:
                part = key
            remaining -= len(part.encode('utf-8'))
            if remaining < 0:
                self._fail(node, 'ALERT_TEMPLATE_MESSAGE', 'Rendered alert message exceeds 4096 UTF-8 bytes.')
            parts.append(part)
        return ''.join(parts)
