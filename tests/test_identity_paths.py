"""Loader aliases and Windows 8.3 names must not change implementation identity."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.execution import identity


class IdentityPathTests(unittest.TestCase):
    def test_loader_filename_does_not_override_the_canonical_source_root(self):
        expected=identity.implementation_hash()
        # The loader can spell this file through an alias outside the root's
        # textual prefix. Hash the canonical root's file, not loader spelling.
        alias=identity.ROOT.parent/"loader-alias"/"packages/execution/identity.py"
        with patch.object(identity,"__file__",str(alias)):
            self.assertEqual(identity.implementation_hash(),expected)

    @unittest.skipUnless(os.name=="nt","Windows 8.3 filesystem alias")
    def test_windows_short_paths_preserve_computation_and_build_identity(self):
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL("kernel32",use_last_error=True)
        api.GetShortPathNameW.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD]
        api.GetShortPathNameW.restype=wintypes.DWORD
        def short(path):
            buffer=ctypes.create_unicode_buffer(32768)
            n=api.GetShortPathNameW(str(path),buffer,len(buffer))
            if not 0<n<len(buffer):raise ctypes.WinError(ctypes.get_last_error())
            return Path(buffer.value)
        expected=identity.implementation_hash();build=identity.build_identity()
        with patch.object(identity,"__file__",str(short(identity.ROOT/"packages/execution/identity.py"))):
            self.assertEqual(identity.implementation_hash(),expected)
            self.assertEqual(identity.implementation_hash(short(identity.ROOT)),expected)
            self.assertEqual(identity.build_identity(short(identity.ROOT)),build)
