"""Bounded output sink and range readers; exact JSON scalars inside Parquet."""

import csv
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from packages.contracts.wire import canonical, loads
from .columnar import connection, quoted


from .trade_index import TradeIndex

class ResultSink:
    def trade_history_factory(self):return TradeIndex(self.directory)
    def __init__(self, objects, directory, *, threads=1, memory_mb=128):
        self.objects, self.directory = objects, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.threads, self.memory_mb = threads, memory_mb
        self.files, self.outputs = [], {}
        self.rows = 0
        self.stream = None
        self.trade_sink=None
        self.cleanups=[]

    def enable_simulation(self,*,indexed=False):
        from .trades import TradeSink
        if self.trade_sink is not None:raise ValueError('Simulation sink is already enabled.')
        self.trade_sink=TradeSink(self.objects,self.directory/'trades',threads=self.threads,memory_mb=self.memory_mb,indexed=indexed)

    def simulation_emit(self,events):self.trade_sink.emit(events)

    def simulation_prefix_events(self):
        from .trades import prefix_events
        return prefix_events(self.trade_sink)

    def validate_simulation(self,broker):
        if getattr(broker,'performance',None) and self.trade_sink.closed_records!=broker.performance.history.count:raise ValueError('Closed trade index differs from the broker history.')
        if (self.trade_sink is None or self.trade_sink.records!=broker.event_count or self.trade_sink.last is None
                or self.trade_sink.last['bar_index']!=broker.count-1 or self.trade_sink.last['time']!=broker.last_time
                or self.trade_sink.last['value']!=broker.statistics()):raise ValueError('Broker state differs from its committed journal boundary.')

    def emit(self, output_id, title, value_type, value, index, timestamp):
        if self.stream is None:
            self.stream = (self.directory / "output.csv").open("w", newline="", encoding="utf-8")
            self.writer = csv.writer(self.stream)
            self.start, self.end = index, index
        info = self.outputs.setdefault(output_id, {"output_id": output_id, "title": title,
            "value_type": asdict(value_type), "count": 0, "null_count": 0, "last": None})
        info["count"] += 1
        info["null_count"] += value is None
        info["last"] = value
        self.writer.writerow((index, timestamp, output_id, canonical(value).decode("utf-8")))
        self.end = index
        self.rows += 1
        if self.rows >= 100_000:
            self.flush()

    def flush(self):
        if self.stream is None:
            return
        self.stream.close(); self.stream = None
        parquet = self.directory / "output.parquet"
        with connection(threads=self.threads, memory_mb=self.memory_mb, temporary=self.directory) as db:
            db.execute(f"COPY (SELECT * FROM read_csv({quoted(self.directory/'output.csv')}, header=false, "
                "columns={'bar_index':'BIGINT','time':'BIGINT','output_id':'VARCHAR','value_json':'VARCHAR'}, nullstr='')) "
                f"TO {quoted(parquet)} (FORMAT PARQUET,COMPRESSION ZSTD)")
        sha = self.objects.publish(parquet)
        self.files.append({"object_id": sha, "start": self.start, "end": self.end + 1, "rows": self.rows})
        parquet.unlink(); (self.directory / "output.csv").unlink()
        self.rows = 0

    def finish(self, summary):
        self.flush()
        if self.trade_sink is not None:summary={**summary,'simulation':{**summary['simulation'],'journal':self.trade_sink.finish()}}
        return {**summary, "outputs": list(self.outputs.values()), "series": self.files}

    def close(self):
        for cleanup in self.cleanups:cleanup()
        self.cleanups=[]
        if self.stream:
            self.stream.close(); self.stream = None
        if self.trade_sink is not None:self.trade_sink.close()

    def checkpoint(self,completed):
        pending=None
        if self.stream:
            self.stream.flush();os.fsync(self.stream.fileno())
            pending={"object_id":self.objects.copy_file(self.directory/"output.csv"),
                "rows":self.rows,"start":self.start,"end":self.end+1}
        value={"schema_version":"openpine.output-prefix.1","completed":completed,
            "files":list(self.files),"outputs":[dict(v) for v in self.outputs.values()],"pending":pending}
        if self.trade_sink is not None:value.update(schema_version='openpine.output-prefix.2',simulation=self.trade_sink.checkpoint(completed))
        return value

    def restore(self,state,completed):
        if self.trade_sink is not None:
            if type(state) is not dict or state.keys()!={'schema_version','completed','files','outputs','pending','simulation'} or state['schema_version']!='openpine.output-prefix.2':
                raise ValueError('Missing strategy output prefix.')
            self.trade_sink.restore(state['simulation'],completed)
            state={k:v for k,v in state.items() if k!='simulation'};state['schema_version']='openpine.output-prefix.1'
        if (self.stream is not None or self.files or self.outputs or type(state) is not dict
                or state.keys()!={"schema_version","completed","files","outputs","pending"}
                or state["schema_version"]!="openpine.output-prefix.1" or state["completed"]!=completed
                or type(state["files"]) is not list or type(state["outputs"]) is not list):
            raise ValueError("Invalid output checkpoint prefix.")
        outputs={}
        for info in state["outputs"]:
            if (type(info) is not dict or info.keys()!={"output_id","title","value_type","count","null_count","last"}
                    or type(info["output_id"]) is not str or info["output_id"] in outputs
                    or type(info["count"]) is not int or info["count"]!=completed
                    or type(info["null_count"]) is not int or not 0<=info["null_count"]<=completed):
                raise ValueError("Invalid output prefix metadata.")
            outputs[info["output_id"]]=dict(info)
        total=0
        for part in [*state["files"],*([state["pending"]] if state["pending"] is not None else [])]:
            if (type(part) is not dict or part.keys()!={"object_id","start","end","rows"}
                    or any(type(part[k]) is not int for k in ("start","end","rows"))
                    or not 0<=part["start"]<part["end"]<=completed or not 1<=part["rows"]<=100000):
                raise ValueError("Invalid output checkpoint part.")
            self.objects.verify(part["object_id"]);total+=part["rows"]
        if total!=sum(info["count"] for info in outputs.values()):
            raise ValueError("Output prefix contains a different number of values.")
        self.files=list(state["files"]);self.outputs=outputs
        if state["pending"] is not None:
            pending=state["pending"]
            if pending["rows"]>=100000:raise ValueError("Output tail should have been sealed.")
            shutil.copyfile(self.objects.verify(pending["object_id"]),self.directory/"output.csv")
            self.rows,self.start,self.end=pending["rows"],pending["start"],pending["end"]-1
            self.stream=(self.directory/"output.csv").open("a",newline="",encoding="utf-8")
            self.writer=csv.writer(self.stream)


def series_rows(objects, summary, start=0, end=None):
    end = summary["observation_count"] if end is None else end
    with connection() as db:
        for part in summary["series"]:
            if part["start"] >= end or part["end"] <= start:
                continue
            cursor = db.execute("SELECT bar_index,time,output_id,value_json FROM read_parquet(?) "
                "WHERE bar_index>=? AND bar_index<? ORDER BY bar_index,output_id",
                [str(objects.verify(part["object_id"])), start, end])
            while batch := cursor.fetchmany(8192):
                for index, timestamp, output_id, value in batch:
                    yield index, timestamp, output_id, loads(value)
