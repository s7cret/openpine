"""Resolve a committed journal sequence to its candle, broker mark and order site."""
from contextlib import closing
from dataclasses import asdict

from packages.contracts.strategy_ir import strategy_nodes
from packages.execution.artifacts import import_program


def episode(reader, job_id, sequence):
    if type(sequence) is not int or not 0 <= sequence < 2**63:
        raise ValueError('Invalid journal sequence.')
    response = reader.summary(job_id)
    simulation = response.get('result', {}).get('simulation')
    if not simulation:
        raise ValueError('This result has no simulation journal.')
    journal = simulation['journal']

    def events(**filters):
        return reader.read_events(reader.objects, journal, **filters)

    with closing(events(start=sequence, limit=1)) as rows:
        selected = next(rows, None)
    if not selected or selected['sequence'] != sequence or selected['kind'] not in ('fill','trade'):
        raise ValueError('Select a committed fill or trade.')
    bar = selected['bar_index']
    fill = selected
    if selected['kind'] == 'trade':
        with closing(events(bar_start=bar,bar_end=bar+1,kinds=('fill',))) as rows:
            fill = next((e for e in rows if e['value']['fill_id'] == selected['value']['exit_fill_id']), None)
        if fill is None:
            raise ValueError('Trade exit fill is missing from its journal.')
    order_id = fill['value']['order_id']
    order = None
    for part in reversed(journal['parts']):
        if part['first_sequence'] > fill['sequence']:
            continue
        with closing(reader.read_events(reader.objects,{**journal,'parts':[part]},bar_end=bar+1,
                                        kinds=('order',))) as rows:
            for event in rows:
                if event['sequence'] <= fill['sequence'] and event['value'].get('order_id') == order_id:
                    order = event
        if order is not None:
            break
    with closing(events(bar_start=bar,bar_end=bar+1,kinds=('mark',))) as rows:
        mark = None
        for event in rows:
            mark = event['value']
    request = reader.repository.request(job_id)
    market = reader.market.source(request['snapshot_id'])
    with closing(market.rows(fill['time'],fill['time']+1,limit=1)) as rows:
        candle = next(rows,None)
    site = order['value'].get('intent',{}).get('call_site_id') if order else None
    program = import_program(reader.objects.get(response['artifact_ref']))
    node = next((n for n in strategy_nodes(program) if n.call_site_id == site),None)
    content = reader.objects.get(request['input_ref'])
    if 'source_ref' in content:
        content = reader.objects.get(content['source_ref'])
    span = asdict(node.span) if node else None
    lines = content.get('source','').splitlines()
    return dict(schema_version='openpine.fill-episode.1', job_id=job_id, snapshot_id=request['snapshot_id'],
                fill=fill, order=order, candle=asdict(candle) if candle else None, bar_close_state=mark,
                span=span, source_line=lines[span['line']-1] if span and 0 < span['line'] <= len(lines) else None)
