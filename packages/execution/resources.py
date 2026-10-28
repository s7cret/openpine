"""Resident memory sampling for the coordinator's process budgets."""

import os
from pathlib import Path


def host_memory():
    """Available physical memory, including reclaimable standby/cache pages.

    Failure is explicit, never confused with zero free bytes. No privilege,
    cache eviction, process termination or allocation is involved.
    """
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            class Status(ctypes.Structure):
                _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD),
                    *[(name,ctypes.c_ulonglong) for name in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]]
            api=ctypes.WinDLL('kernel32',use_last_error=True)
            api.GlobalMemoryStatusEx.argtypes=[ctypes.POINTER(Status)]
            api.GlobalMemoryStatusEx.restype=wintypes.BOOL
            value=Status();value.length=ctypes.sizeof(value)
            if not api.GlobalMemoryStatusEx(ctypes.byref(value)):return None
            return {'total_bytes':value.total,'available_bytes':value.available,'source':'windows.physical'}
        fields={parts[0].rstrip(':'):int(parts[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                if len(parts:=line.split())==3 and parts[2]=='kB'}
        total,available=fields['MemTotal'],fields['MemAvailable']
        source='linux.MemAvailable'
        maximum=Path('/sys/fs/cgroup/memory.max');current=Path('/sys/fs/cgroup/memory.current')
        if maximum.is_file() and current.is_file():
            raw=maximum.read_text().strip()
            if raw!='max':
                limit=int(raw);used=int(current.read_text().strip())
                total=min(total,limit);available=min(available,max(0,limit-used));source+=';cgroup2'
        return {'total_bytes':total,'available_bytes':available,'source':source}
    except (OSError,ValueError,KeyError,AttributeError):return None


def resident_bytes(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            return 0
        try:
            value = Counters(); value.cb = ctypes.sizeof(value)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(value), value.cb):
                raise ctypes.WinError(ctypes.get_last_error())
            return value.WorkingSetSize
        finally:
            api.CloseHandle(handle)
    try:
        return int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except FileNotFoundError:
        return 0
