"""Bind event indices to immutable plans and runtime call sites."""
from packages.contracts.corporate import ActionIndex, BoundActions, requests, requirements, rate_requirements, fail
from packages.contracts.financial import stream_key
from dataclasses import replace
from packages.contracts.model import OpenPineError
from packages.contracts.calendar import next_open
from packages.contracts.contexts import MarketContext


def attach(evaluation, value, symbol, timeframe, chart_currency=None, fx_rates=None, *, replay=False):
    nodes = requests(evaluation.program)
    fx = rate_requirements(evaluation.program)
    try:
        if not nodes and not fx:
            if value is not None:fail('ACTION_UNUSED','The program has no corporate-action requests.')
            if fx_rates is not None:fail('FX_UNUSED','The program has no currency requests.')
            return
        if nodes and value is None:
            fail('ACTION_SNAPSHOT', 'Select a pinned corporate-action snapshot.')
        if value is not None and not nodes:fail('ACTION_UNUSED','The program has no corporate-action requests.')
        from packages.contracts.fx import Rates
        if fx and fx_rates is None:fail('FX_SNAPSHOT','Select a pinned FX snapshot.')
        rates = Rates(fx_rates) if fx_rates is not None else None
        index = ActionIndex(value) if value is not None and not isinstance(value, ActionIndex) else value
        primary = MarketContext('primary',symbol,timeframe,'corporate-plan')
        evaluation.corporate = BoundActions(index,timeframe,requirements(evaluation.program,evaluation.input_settings,primary),chart_currency,rates,fx,replay=replay)
    except OpenPineError as error:
        raise OpenPineError(replace(error.diagnostic,language_version=evaluation.program.identity.language_version)) from error


def admit_snapshot(program, request, manifest, objects):
    identity = manifest["identity"]
    ref = request.get('corporate_snapshot_id')
    if requests(program) and not ref:
        fail('ACTION_SNAPSHOT','Select a pinned corporate-action snapshot.')
    from packages.contracts.fx import Rates
    fx_ref = request.get('fx_snapshot_id')
    fx = rate_requirements(program)
    if fx and not fx_ref:fail('FX_SNAPSHOT','Select a pinned FX snapshot.')
    rates = Rates(objects.get(fx_ref)) if fx_ref else None
    for row in fx:
        rates.bind(row['base'],row['quote'],row['ignore_invalid_currency'],
                   (identity['first_ms'],next_open(identity['last_ms'],identity['timeframe'])))
    index = ActionIndex(objects.get(ref)) if ref else None
    primary = MarketContext('primary',identity['symbol'],identity['timeframe'],'corporate-plan')
    for req in requirements(program,request.get('settings'),primary):
        instrument = manifest['provenance'].get('environment',{}).get('instrument',{})
        currency = req['currency'] or (instrument.get('currency') if req['default_chart_currency'] else '')
        stream = index.select_request(req, currency, rates)
        if stream and (identity['first_ms'] < stream[0]['start_ms'] or
                       next_open(identity['last_ms'],identity['timeframe']) > stream[0]['end_ms']):
            fail('ACTION_COVERAGE','Snapshot coverage does not include the requested candle interval.')


def planned_rates(program, settings, primary, actions, chart_currency, snapshot_id, start, end):
    rows = rate_requirements(program)
    if actions is not None:
        for req in requirements(program, settings, primary):
            stream = actions.streams.get(stream_key(req))
            target = req['currency'] or (chart_currency if req['default_chart_currency'] else None)
            if stream and stream[0]['currency'] and target and target != stream[0]['currency']:
                rows.append(dict(call_site_id=req['call_site_id'],base=stream[0]['currency'],quote=target,
                                 ignore_invalid_currency=False))
    return [{**row, 'snapshot_id':snapshot_id, 'start_ms':start, 'end_ms':end} for row in rows]
