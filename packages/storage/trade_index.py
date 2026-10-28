"""Disposable, disk-backed closed-trade lookup; the immutable journal is authoritative."""
from collections import OrderedDict
from pathlib import Path
import hashlib,json,tempfile,shutil,weakref
import apsw
from packages.contracts.wire import canonical

EMPTY=hashlib.sha256(b'openpine.closed-trades.1').hexdigest()
def _cleanup(db,path,root):
 db.close()
 target=path.resolve()
 if target.parent!=root or not target.name.startswith('openpine-trades-'):raise ValueError('Unsafe temporary trade index path.')
 shutil.rmtree(target)

class TradeIndex:
 def __init__(self,directory=None):
  root=Path(directory if directory is not None else tempfile.gettempdir()).resolve();root.mkdir(parents=True,exist_ok=True)
  self.directory=Path(tempfile.mkdtemp(prefix='openpine-trades-',dir=root));self.db=apsw.Connection(str(self.directory/'index.sqlite3'))
  self.finalizer=weakref.finalize(self,_cleanup,self.db,self.directory,root)
  self.db.execute('PRAGMA cache_size=-1024');self.db.execute('PRAGMA temp_store=FILE')
  self.db.execute('CREATE TABLE trades (number INTEGER PRIMARY KEY, value TEXT NOT NULL)')
  self.db.execute('BEGIN')  # Private derived index; durability belongs to the journal.
  self.count=0;self.sha256=EMPTY;self.cache=OrderedDict()
 def append(self,row):
  if type(row.get('closed_index')) is not int or row['closed_index']!=self.count:raise ValueError('Closed trade index must be sequential.')
  raw=canonical(row)
  if self.count%1000==0 or len(raw)>65536:
   from .disk import guard
   guard(self.directory,max(1024**2,2*len(raw)))
  self.db.execute('INSERT INTO trades VALUES (?,?)',(self.count,raw.decode()))
  self.sha256=hashlib.sha256(bytes.fromhex(self.sha256)+raw).hexdigest();self.count+=1
  self.cache[self.count-1]=dict(row)
  if len(self.cache)>128:self.cache.popitem(last=False)
 def read(self,number):
  if type(number) is not int or not 0<=number<self.count:return None
  value=self.cache.get(number)
  if value is None:
   row=self.db.execute('SELECT value FROM trades WHERE number=?',(number,)).fetchone();value=json.loads(row[0]);self.cache[number]=value
   if len(self.cache)>128:self.cache.popitem(last=False)
  else:self.cache.move_to_end(number)
  return dict(value)
 def identity(self):return {'count':self.count,'sha256':self.sha256}
 def close(self):
  if self.db is not None:self.finalizer();self.db=None
