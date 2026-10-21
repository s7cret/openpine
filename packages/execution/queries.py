"""Result queries and compatibility exports, independent of storage adapters."""

from dataclasses import asdict

from packages.contracts import OpenPineError
from packages.execution.preparation import invalid
from packages.execution.limits import bounded


def legacy_response(objects, response, source, read_series):
    """Explicit small-result compatibility/export adapter, outside the runtime."""
    from packages.execution.artifacts import import_program
    from packages.contracts import HistoricalData
    from packages.marketdata import data_identity
    if response["status"] != "executed" or "series" not in response.get("result", {}):
        return response
    summary = response["result"]
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
    if summary["schema_version"]=="openpine.run-summary.4":
        result.update(schema_version="openpine.run-result.3",**{k:summary[k] for k in ("primary_context","contexts","requests")})
    return {"status": "executed", "python": response["python"], "identity": summary["identity"],
        "program_sha256": summary["program_sha256"], "ir": asdict(program), "result": result,
        "snapshot_sha256": response["snapshot_sha256"]}


class ResultReader:
    def __init__(self, repository, objects, market, read_series, limiter):
        self.repository, self.objects, self.market, self.read_series = repository, objects, market, read_series
        self.limiter = limiter

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
                self.market.source(self.repository.request(job_id)["snapshot_id"]), self.read_series)
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
        return {"schema_version":"openpine.chart-window.1", "start":start,"total":source.identity.count,
            "bars":bars,"outputs":[{**info,"values":values[info["output_id"]]} for info in summary["outputs"]]}
