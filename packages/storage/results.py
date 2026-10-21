"""Bounded output sink and range readers; exact JSON scalars inside Parquet."""

import csv
from dataclasses import asdict
from pathlib import Path

from packages.contracts.wire import canonical, loads
from .columnar import connection, quoted


class ResultSink:
    def __init__(self, objects, directory, *, threads=1, memory_mb=128):
        self.objects, self.directory = objects, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.threads, self.memory_mb = threads, memory_mb
        self.files, self.outputs = [], {}
        self.rows = 0
        self.stream = None

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
        return {**summary, "outputs": list(self.outputs.values()), "series": self.files}

    def close(self):
        if self.stream:
            self.stream.close(); self.stream = None


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
