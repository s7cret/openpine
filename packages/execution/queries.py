"""Result queries and compatibility exports, independent of storage adapters."""

from dataclasses import asdict

from packages.contracts import OpenPineError
from packages.execution.preparation import invalid
from packages.execution.limits import bounded


def legacy_response(objects, response, source, read_series, read_events=None):
    """Explicit small-result compatibility/export adapter, outside the runtime."""
    from packages.execution.artifacts import import_program
    from packages.contracts import HistoricalData
    from packages.marketdata import data_identity
    if response["status"] != "executed" or "series" not in response.get("result", {}):
        return response
    summary = response["result"]
    if summary.get('simulation',{}).get('journal',{}).get('records',0)>100000:return response
    if summary["observation_count"] > 100_000 or sum(info["count"] for info in summary["outputs"]) > 100_000:
        return response
    program = import_program(objects.get(response["artifact_ref"]))
    values = {info["output_id"]: [] for info in summary["outputs"]}
    for _, _, output_id, value in read_series(objects, summary):
        values[output_id].append(value)
    result = {k: summary[k] for k in ("observation_count", "declaration", "identity", "program_sha256")}
    # run-result.2 identifies canonical historical JSON, whereas summary.3
    # explicitly identifies the versioned row stream. Convert the identity along
    # with the representation so a downloaded JSON snapshot replays identically.
    bars = tuple(source)
    legacy_data = HistoricalData(source.identity.context_id, source.identity.symbol,
        source.identity.timeframe, bars, schema_version=source.manifest["metadata"]["bar_schema"])
    result.update(schema_version="openpine.run-result.2", data=asdict(data_identity(legacy_data)), outputs=[{
            "output_id": info["output_id"], "title": info["title"], "value_type": info["value_type"],
            "values": values[info["output_id"]]} for info in summary["outputs"]])
    if 'primary_context' in summary:
        result.update(schema_version="openpine.run-result.3",**{k:summary[k] for k in ("primary_context","contexts","requests")})
    if 'simulation' in summary:
        if read_events is None:raise ValueError('Missing journal reader.')
        simulation=summary['simulation']
        if simulation['policy']['profile_id'] in ('openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1'):
            from .exit_views import export_events
            events=export_events(read_events(objects,simulation['journal']))
            if events is None:return response
        else:events=list(read_events(objects,simulation['journal']))
        result.update(schema_version='openpine.run-result.4',primary_context=result.get('primary_context'),contexts=result.get('contexts',[]),requests=result.get('requests',[]),
            simulation={**{k:v for k,v in simulation.items() if k!='journal'},'events':events})
    if 'settings' in summary:
        result.update(schema_version='openpine.run-result.5',settings=summary['settings'],primary_context=result.get('primary_context'),
            contexts=result.get('contexts',[]),requests=result.get('requests',[]),simulation=result.get('simulation'))
    if 'evaluation_window' in summary:result.update(schema_version='openpine.run-result.6',evaluation_window=summary['evaluation_window'])
    return {"status": "executed", "python": response["python"], "identity": summary["identity"],
        "program_sha256": summary["program_sha256"], "ir": asdict(program), "result": result,
        "snapshot_sha256": response["snapshot_sha256"]}


class ResultReader:
    def __init__(self, repository, objects, market, read_series, limiter, read_events=None):
        self.repository, self.objects, self.market, self.read_series = repository, objects, market, read_series
        self.limiter = limiter
        self.read_events=read_events

    @bounded("read")
    def summary(self, job_id):
        state = self.repository.get(job_id)
        if not state["result_id"]:
            invalid("This job has no committed result.", "RESULT_UNAVAILABLE")
        try:
            return self.objects.get(state["result_id"], max_bytes=32*1024*1024)
        except ValueError as error:
            invalid(str(error), "RESULT_INVALID")

    @bounded("read")
    def result(self, job_id):
        response = self.summary(job_id)
        try:
            return legacy_response(self.objects, response,
                self.market.source(self.repository.request(job_id)["snapshot_id"]), self.read_series,self.read_events)
        except OpenPineError:
            raise
        except ValueError as error:
            invalid(str(error), "RESULT_INVALID")

    @bounded("read")
    def chart(self, job_id, start=0, limit=2000):
        if type(start) is not int or type(limit) is not int or start < 0 or not 1 <= limit <= 5000:
            invalid("Chart requires start>=0 and 1..5000 rows.")
        response = self.summary(job_id)
        if response["status"] != "executed":
            return response
        summary = response["result"]
        if "series" not in summary:
            invalid("Legacy result: use full export.")
        source = self.market.source(response["snapshot_sha256"])
        bars = [asdict(bar) for bar in source.rows(offset=start, limit=limit)]
        values = {info["output_id"]: [] for info in summary["outputs"]}
        for _, _, output_id, value in self.read_series(self.objects, summary, start, start+limit):
            values[output_id].append(value)
        result={"schema_version":"openpine.chart-window.1", "start":start,"total":source.identity.count,
            "bars":bars,"outputs":[{**info,"values":values[info["output_id"]]} for info in summary["outputs"]]}
        from packages.contracts.protection import PROFILE
        simulation=summary.get('simulation')
        if simulation and simulation['policy']['profile_id'] in (PROFILE,'openpine.partial-broker.1','openpine.pending-entry-broker.1','openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1'):
            grouped=simulation['policy']['profile_id'] in ('openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1')
            extended=simulation['policy']['profile_id'] in ('openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1')
            lots=simulation['policy']['profile_id'] in ('openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1')
            pending=simulation['policy']['profile_id'] in ('openpine.pending-entry-broker.1','openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1')
            partial=simulation['policy']['profile_id'] in ('openpine.partial-broker.1','openpine.pending-entry-broker.1','openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1')
            event_source=self.read_events(self.objects,simulation['journal'],0,5001,bar_start=max(0,start-1),bar_end=start+limit,
                kinds=('mark','fill','protection_trigger','cancelled','order','protection_activated','cancel_command','ignored',*(['trade','reservation_reduced'] if partial else []),*(['entry_activated','entry_trigger','entry_deferred'] if pending else []),*(['oca_filled','oca_reduced','replaced'] if grouped else []),*(['trailing_activated','trailing_updated'] if extended else []),*(['quantity_resolved','commission_calculated'] if simulation['policy']['profile_id'] in ('openpine.economics-broker.1','openpine.risk-broker.1') else []),*(['risk_triggered','risk_period','risk_adjusted','risk_blocked','risk_liquidation'] if simulation['policy']['profile_id']=='openpine.risk-broker.1' else [])))
            if extended:
                from .exit_views import chart_events
                visible,truncated,event_bytes=chart_events(event_source)
            else:
                events=list(event_source);visible=events[:5000];truncated=len(events)>5000
            result.update(schema_version='openpine.chart-window.9' if simulation['policy']['profile_id']=='openpine.risk-broker.1' else 'openpine.chart-window.8' if simulation['policy']['profile_id'] in ('openpine.economics-broker.1','openpine.risk-broker.1') else 'openpine.chart-window.7' if extended else 'openpine.chart-window.6' if grouped else 'openpine.chart-window.5' if lots else 'openpine.chart-window.4' if pending else 'openpine.chart-window.3' if partial else 'openpine.chart-window.2',protection={'policy':simulation['policy'],
                'events':[{**e,'value':{'protections':e['value'].get('protections',[]),**({'entries':e['value'].get('entries',[])} if pending else {}),**({k:e['value'][k] for k in ('lots','position_size','position_avg_price','open_lots')} if lots else {}),**({'order_groups':e['value']['order_groups']} if grouped else {}),**({'exit_states':e['value']['exit_states']} if extended else {}),**({'economics':e['value']['economics']} if simulation['policy']['profile_id'] in ('openpine.economics-broker.1','openpine.risk-broker.1') else {}),**({'risk':e['value']['risk']} if simulation['policy']['profile_id']=='openpine.risk-broker.1' else {})}} if partial and e['kind']=='mark' else {**e,'value':{'protection':e['value'].get('protection')}} if e['kind']=='mark' else e for e in visible],
                'truncated':truncated,**({'event_bytes':event_bytes,'event_byte_budget':2*1024*1024} if extended else {}),'next_sequence':visible[-1]['sequence']+1 if visible else 0,
                'start_bar':start,'end_bar':start+len(bars)})
        return result

    @bounded('read')
    def ledger(self,job_id,start=0,limit=2000):
        if type(start) is not int or type(limit) is not int or start<0 or not 1<=limit<=5000:invalid('Ledger requires start>=0 and 1..5000 events.')
        response=self.summary(job_id)
        if response['status']!='executed' or 'simulation' not in response['result']:invalid('This job has no committed simulation ledger.')
        journal=response['result']['simulation']['journal'];events=list(self.read_events(self.objects,journal,start,limit))
        return {'schema_version':'openpine.ledger-window.1','start':start,'total':journal['records'],
            'next':events[-1]['sequence']+1 if events else min(start,journal['records']),'events':events}
