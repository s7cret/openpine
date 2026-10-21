"""Strict finite UTF-8 JSON and atomic local persistence, without domain imports."""

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def loads(raw, *, max_bytes=16 * 1024 * 1024):
    if type(raw) is str:
        raw = raw.encode("utf-8")
    if type(raw) is not bytes or len(raw) > max_bytes:
        raise ValueError("JSON exceeds the input limit.")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def number(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("Nonfinite JSON number.")
        return value

    def constant(token):
        raise ValueError(f"Nonfinite JSON constant: {token}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=number, parse_constant=constant)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid JSON encoding or nesting.") from error


def read_json(path, *, max_bytes=16 * 1024 * 1024):
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        # FILE_SHARE_DELETE lets an atomic writer replace the pathname while this
        # reader finishes reading its original, complete file handle.
        handle = api.CreateFileW(str(Path(path).resolve()), 0x80000000, 7, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            api.CloseHandle(handle)
            raise
        stream = os.fdopen(fd, "rb")
    else:
        stream = Path(path).open("rb")
    with stream:
        return loads(stream.read(max_bytes + 1), max_bytes=max_bytes)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/antivirus may briefly hold a handle without delete sharing.
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        Path(temporary).unlink(missing_ok=True)


def immutable_json(path, value):
    """Publish a complete new file once; concurrent readers never see a prefix."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError("Existing immutable file differs from its content identity.")
    finally:
        Path(temporary).unlink(missing_ok=True)
