"""CLI file adapter for the same snapshot context bindings used by HTTP."""

from dataclasses import asdict
from packages.contracts.contexts import ContextBinding,MarketContext
from packages.contracts.wire import read_json
from packages.marketdata.providers import input_snapshot


def load_context_inputs(manager,path,output=None):
    value=read_json(path)
    if (type(value) is not dict or value.keys()!={"schema_version","primary_context","contexts"}
            or value["schema_version"]!="openpine.context-inputs.1" or type(value["contexts"]) is not list
            or len(value["contexts"])>7):raise ValueError("Invalid context inputs file.")
    if output and output.resolve()==path.resolve():raise ValueError("Output must not overwrite context inputs.")
    primary=MarketContext.from_payload(value["primary_context"]);bindings=[]
    for row in value["contexts"]:
        if type(row) is not dict or row.keys() not in ({"context","snapshot_id"},{"context","bars"}):raise ValueError("Invalid context file entry.")
        context=MarketContext.from_payload(row["context"])
        if "bars" in row:
            if type(row["bars"]) is not str:raise ValueError("Context bars path must be text.")
            bars=(path.parent/row["bars"]).resolve()
            if output and output.resolve()==bars:raise ValueError("Output must not overwrite context bars.")
            sha=manager.market.save(input_snapshot(read_json(bars)))
        else:sha=row["snapshot_id"]
        bindings.append(asdict(ContextBinding(context,sha)))
    return {"primary_context":asdict(primary),"contexts":bindings}
