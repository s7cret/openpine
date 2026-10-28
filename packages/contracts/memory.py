"""Conservative charged storage; independent of the process RSS hard limit.

Charges include Python objects and container slots on the supported CPython
builds. Exact integers admit up to 4,300 bits; strings up to 65,536 characters.
Logical history cells remain a separate admission limit.
"""
KINDS={'value':96,'index':64,'pair':128,'exact':640,'string':262400,'reference':384,
       'preview':256,'state':4096,'array_object':2048,'array_cell':96,'byte':1,'string_slot':256}

def string_payload(value):return 4*len(value) if type(value) is str else 0

def charge(kind,cells=1):return KINDS[kind]*cells

def value_kind(base):return 'string' if base=='string' else 'reference' if base and base.startswith('array.') else 'value'

def job_memory(memory_mb,io_slots=2):
    if type(memory_mb) is not int or memory_mb<1 or type(io_slots) is not int or io_slots<1:
        raise ValueError('Invalid job memory budget.')
    total=memory_mb*1024**2
    # One state ledger includes arrays, histories, previews and restore staging.
    # IO caps are shared by the actual concurrent reader/writer slot count.
    state=total//2;io=total//3;base=total-state-io
    return dict(profile_id='openpine.job-memory.1',total_bytes=total,
        state_and_restore_bytes=state,restore_reserve_bytes=state//2,
        io_bytes=io,base_and_transient_bytes=base,io_slots=io_slots,
        per_io_mb=min(128,io//io_slots//1024**2),rss_hard_limit_bytes=total,
        charge_is_rss=False)
