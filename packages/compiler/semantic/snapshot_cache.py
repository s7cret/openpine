"""Bounded private validated templates; callers always receive isolated snapshots."""
import copy
import hashlib
import sys
import threading
from collections import OrderedDict
from . import specification

_lock=threading.RLock()
_entries=OrderedDict()
_hits=_misses=0
MAX_ENTRIES=2

def fingerprint(root):
    files=[]
    for path in sorted((root/'spec').rglob('*')):
        if path.is_symlink():raise specification.SpecError('SPEC_PATH','Symlinks are not admitted in the specification cache.')
        if path.is_file():files.append((path.relative_to(root).as_posix(),hashlib.sha256(path.read_bytes()).hexdigest()))
    return specification.value_sha({'python':list(sys.version_info[:3]),'files':files})

def load_snapshot(root=None):
    global _hits,_misses
    specification.require_python();root=(root or specification.ROOT).resolve()
    with _lock:
        key=(str(root),fingerprint(root))
        if key in _entries:
            _hits+=1;_entries.move_to_end(key)
            return copy.deepcopy(_entries[key])
        snapshot=specification.load_snapshot(root)
        if fingerprint(root)!=key[1]:raise specification.SpecError('SPEC_HASH','Specification changed while it was being admitted.')
        _misses+=1;_entries[key]=snapshot
        while len(_entries)>MAX_ENTRIES:_entries.popitem(last=False)
        return copy.deepcopy(snapshot)

def clear():
    global _hits,_misses
    with _lock:_entries.clear();_hits=_misses=0

def info():
    with _lock:return {'entries':len(_entries),'limit':MAX_ENTRIES,'hits':_hits,'validations':_misses}
