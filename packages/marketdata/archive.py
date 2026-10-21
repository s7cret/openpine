"""Explicit Binance monthly archive source; SHA256 and bounded ZIP/CSV parsing."""

import csv
import hashlib
import io
import re
import urllib.error
import urllib.request
from urllib.parse import quote
import zipfile
from datetime import UTC, datetime

from .history import HistoryPage, normalize_rows
from .timeframes import INTERVALS, floor_open, next_open

BASE='https://data.binance.vision/data/spot/monthly/klines'


def download(url, control, max_bytes):
    if not url.startswith(BASE+'/'): raise ValueError('Unsupported archive endpoint.')
    for attempt in range(4):
        control.check()
        try:
            with urllib.request.urlopen(url,timeout=min(10,max(.1,control.deadline-control.clock()))) as response:
                parts=[];size=0
                while True:
                    control.check();part=response.read(min(65536,max_bytes+1-size))
                    if not part:break
                    parts.append(part);size+=len(part)
                    if size>max_bytes:raise ValueError('Archive response exceeds its byte budget.')
                return b''.join(parts)
        except urllib.error.HTTPError as error:
            status=error.code;error.close()
            if status==404: raise FileNotFoundError('Monthly archive is not available; select the REST source.') from error
            if status not in (429,500,502,503,504) or attempt==3: raise OSError('Archive HTTP request failed.') from error
        except (urllib.error.URLError,TimeoutError,ConnectionError):
            if attempt==3: raise
        control.wait(.25*2**attempt)
    raise RuntimeError('Unreachable archive retry state.')


class ArchiveHistory:
    def __init__(self, rest_provider, *, downloader=download):self.rest,self.downloader=rest_provider,downloader

    def pages(self,request,*,start,stop,as_of,control):
        month=floor_open(start,'M');confirmed=min(stop,floor_open(as_of,request.timeframe));cursor=start
        while cursor<confirmed:
            control.check()
            date=datetime.fromtimestamp(month/1000,UTC);month_end=next_open(month,'M')
            if month_end>as_of:raise FileNotFoundError('The current monthly archive is not final; select REST.')
            interval='1mo' if request.timeframe=='M' else INTERVALS[request.timeframe]
            name=f'{request.instrument.symbol}-{interval}-{date:%Y-%m}.zip'
            url=f'{BASE}/{quote(request.instrument.symbol,safe="")}/{interval}/{quote(name,safe="")}'
            checksum=self.downloader(url+'.CHECKSUM',control,512).decode('utf-8').strip()
            match=re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?'+re.escape(name),checksum)
            if match is None:raise ValueError('Invalid archive checksum document.')
            raw=self.downloader(url,control,32*1024*1024)
            sha=hashlib.sha256(raw).hexdigest()
            if sha!=match[1].lower():raise ValueError('Archive SHA256 mismatch.')
            end=min(confirmed,month_end);rows=[]
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries=archive.infolist()
                if (len(entries)!=1 or entries[0].filename!=name[:-4]+'.csv'
                        or entries[0].file_size>128*1024*1024 or entries[0].flag_bits&1):
                    raise ValueError('Unexpected or oversized archive contents.')
                with archive.open(entries[0]) as stream,io.TextIOWrapper(stream,encoding='utf-8',newline='') as text:
                    for number,row in enumerate(csv.reader(text)):
                        if number>100000:raise ValueError('Archive exceeds the bounded monthly row count.')
                        if number%1000==0:control.check()
                        if len(row)!=12:raise ValueError('Malformed archive CSV row.')
                        timestamp=int(row[0]);close=int(row[6])
                        # Binance Spot archives use microseconds starting January 2025.
                        if date.year>=2025:
                            if timestamp%1000:raise ValueError('Archive open is not an exact millisecond.')
                            timestamp//=1000;close//=1000
                        if not month<=timestamp<month_end:raise ValueError('Archive row lies outside its named month.')
                        if cursor<=timestamp<end:rows.append([timestamp,*row[1:6],close,*row[7:]])
            bars=normalize_rows(rows,request,cursor,end,as_of,100000)
            yield HistoryPage(cursor,end,bars,sha)
            cursor=end;month=month_end
