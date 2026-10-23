"""Append-only broker journal in bounded Parquet blocks with exact JSON decimals."""

import csv
import os
import shutil
from pathlib import Path
from packages.contracts.wire import canonical,loads
from .columnar import connection,quoted


class TradeSink:
    def __init__(self,objects,directory,*,threads=1,memory_mb=128):
        self.objects,self.directory=objects,Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.threads,self.memory_mb=threads,memory_mb
        self.files=[];self.rows=0;self.records=0;self.stream=None;self.last=None

    def emit(self,events):
        for event in events:
            if event['sequence']!=self.records:raise ValueError('Journal append sequence differs from broker state.')
            if self.stream is None:
                self.stream=(self.directory/'journal.csv').open('w',newline='',encoding='utf-8');self.writer=csv.writer(self.stream)
                self.start=self.records;self.first_bar=event['bar_index']
            self.writer.writerow((event['sequence'],event['bar_index'],event['time'],event['kind'],canonical(event['value']).decode('utf-8')))
            self.last=event;self.rows+=1;self.records+=1;self.last_bar=event['bar_index']
            if self.rows==100000:self.flush()

    def part(self,object_id):
        return {'object_id':object_id,'first_sequence':self.start,'rows':self.rows,'start':self.first_bar,'end':self.last_bar+1}

    def flush(self):
        if self.stream is None:return
        self.stream.close();self.stream=None
        parquet=self.directory/'journal.parquet'
        with connection(threads=self.threads,memory_mb=self.memory_mb,temporary=self.directory) as db:
            db.execute(f"COPY (SELECT * FROM read_csv({quoted(self.directory/'journal.csv')}, header=false, "
                "columns={'sequence':'BIGINT','bar_index':'BIGINT','time':'BIGINT','kind':'VARCHAR','event_json':'VARCHAR'},"
                "nullstr='',auto_detect=false,buffer_size=262144,max_line_size=65536)) "
                f"TO {quoted(parquet)} (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 2048)")
        self.files.append(self.part(self.objects.publish(parquet)));parquet.unlink();(self.directory/'journal.csv').unlink();self.rows=0

    def finish(self):
        self.flush()
        return {'schema_version':'openpine.trade-journal.1','records':self.records,'parts':self.files}

    def close(self):
        if self.stream:self.stream.close();self.stream=None

    def checkpoint(self,completed):
        pending=None
        if self.stream:
            self.stream.flush();os.fsync(self.stream.fileno());pending=self.part(self.objects.copy_file(self.directory/'journal.csv'))
        return {'schema_version':'openpine.trade-prefix.1','completed':completed,'records':self.records,
            'files':list(self.files),'pending':pending,'last':self.last}

    def restore(self,state,completed):
        if (self.records or self.stream or type(state) is not dict or state.keys()!={'schema_version','completed','records','files','pending','last'}
                or state['schema_version']!='openpine.trade-prefix.1' or type(state['completed']) is not int or state['completed']!=completed
                or type(state['records']) is not int or not completed<=state['records']<=100*completed or type(state['files']) is not list):
            raise ValueError('Invalid broker journal prefix.')
        sequence=0
        for part in [*state['files'],*([state['pending']] if state['pending'] is not None else [])]:
            if (type(part) is not dict or part.keys()!={'object_id','first_sequence','rows','start','end'}
                    or any(type(part[k]) is not int for k in ('first_sequence','rows','start','end'))
                    or part['first_sequence']!=sequence or not 1<=part['rows']<=100000 or not 0<=part['start']<part['end']<=completed):
                raise ValueError('Invalid journal block range/sequence.')
            sequence+=part['rows'];self.objects.verify(part['object_id'])
        if sequence!=state['records']:raise ValueError('Journal record count differs from its blocks.')
        last=state['last']
        if (type(last) is not dict or last.keys()!={'sequence','bar_index','time','kind','value'} or last['kind']!='mark'
                or type(last['sequence']) is not int or last['sequence']!=sequence-1 or type(last['bar_index']) is not int
                or last['bar_index']!=completed-1 or type(last['time']) is not int):raise ValueError('Journal boundary is not a completed mark.')
        self.files=list(state['files']);self.records=sequence;self.last=last
        if state['pending'] is not None:
            p=state['pending']
            if p['rows']>=100000:raise ValueError('Full journal tail should have been sealed.')
            shutil.copyfile(self.objects.verify(p['object_id']),self.directory/'journal.csv')
            self.rows,self.start,self.first_bar,self.last_bar=p['rows'],p['first_sequence'],p['start'],p['end']-1
            self.stream=(self.directory/'journal.csv').open('a',newline='',encoding='utf-8');self.writer=csv.writer(self.stream)


def event_rows(objects,journal,start=0,limit=None,*,bar_start=None,bar_end=None,kinds=None):
    remaining=journal['records'] if limit is None else limit
    with connection() as db:
        for part in journal['parts']:
            if remaining<=0:return
            if part['first_sequence']+part['rows']<=start:continue
            if bar_start is not None and part['end']<=bar_start:continue
            if bar_end is not None and part['start']>=bar_end:continue
            conditions=['sequence>=?'];parameters=[str(objects.verify(part['object_id'])),start]
            if bar_start is not None:conditions.append('bar_index>=?');parameters.append(bar_start)
            if bar_end is not None:conditions.append('bar_index<?');parameters.append(bar_end)
            if kinds:
                conditions.append('kind IN ('+','.join('?' for _ in kinds)+')');parameters.extend(kinds)
            parameters.append(remaining)
            cursor=db.execute('SELECT sequence,bar_index,time,kind,event_json FROM read_parquet(?) WHERE '+' AND '.join(conditions)+' ORDER BY sequence LIMIT ?',parameters)
            while remaining>0 and (rows:=cursor.fetchmany(min(8192,remaining))):
                for seq,index,time,kind,value in rows:
                    yield {'sequence':seq,'bar_index':index,'time':time,'kind':kind,'value':loads(value)}
                    remaining-=1
