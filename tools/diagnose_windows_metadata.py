#!/usr/bin/env python3
"""Watch freshly closed files for metadata changes made by something outside this process (Windows).

Diagnostic only: the quarantine bounded-read and ciphertext-inventory guards compare file ID, size,
LastWriteTime and ChangeTime, and on Windows they have twice refused a file that the test itself was
not touching. This tool writes synthetic files in a scratch directory, closes them, then samples
those four fields through a read-only handle and reports any change and how long after close it
appeared. It changes no production code, guard, state or credential.

    python tools/diagnose_windows_metadata.py [--dir DIR] [--files 50] [--seconds 3]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes as w
from pathlib import Path


class _BasicInfo(ctypes.Structure):
    _fields_ = [('CreationTime', ctypes.c_longlong), ('LastAccessTime', ctypes.c_longlong),
                ('LastWriteTime', ctypes.c_longlong), ('ChangeTime', ctypes.c_longlong),
                ('FileAttributes', w.DWORD)]


class _StandardInfo(ctypes.Structure):
    _fields_ = [('AllocationSize', ctypes.c_longlong), ('EndOfFile', ctypes.c_longlong),
                ('NumberOfLinks', w.DWORD), ('DeletePending', w.BOOLEAN), ('Directory', w.BOOLEAN)]


class _IdInfo(ctypes.Structure):
    _fields_ = [('VolumeSerialNumber', ctypes.c_ulonglong), ('FileId', ctypes.c_ubyte * 16)]


def _kernel() -> ctypes.WinDLL:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    return kernel


def _sample(kernel: ctypes.WinDLL, path: Path) -> dict[str, object]:
    # FILE_READ_ATTRIBUTES only, full sharing: the observer must not itself cause a change.
    handle = kernel.CreateFileW(str(path), 0x80, 0x7, None, 3, 0x02000000, None)
    if handle in (None, w.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        basic, standard, ident = _BasicInfo(), _StandardInfo(), _IdInfo()
        for kind, info in ((0, basic), (1, standard), (18, ident)):
            if not kernel.GetFileInformationByHandleEx(handle, kind, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        return {'id': bytes(ident.FileId).hex(), 'size': standard.EndOfFile,
                'last_write': basic.LastWriteTime, 'change': basic.ChangeTime,
                'attributes': basic.FileAttributes}
    finally:
        kernel.CloseHandle(handle)


def run(directory: Path, files: int, seconds: float) -> dict[str, object]:
    kernel = _kernel()
    closed: dict[Path, tuple[float, dict[str, object]]] = {}
    for index in range(files):
        path = directory / f'probe-{index:03d}.bin'
        with path.open('wb') as stream:
            stream.write(os.urandom(64 * 1024))
        closed[path] = (time.perf_counter(), _sample(kernel, path))
    changes = []
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline and closed:
        for path, (at, first) in list(closed.items()):
            now = _sample(kernel, path)
            changed = [field for field in first if now[field] != first[field]]
            if changed:
                changes.append({'file': path.name, 'fields': changed,
                                'ms_after_close': round((time.perf_counter() - at) * 1000, 1)})
                del closed[path]
        time.sleep(0.005)
    return {'directory': str(directory), 'files': files, 'seconds': seconds,
            'changed_files': len(changes), 'changes': changes}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', type=Path, help='parent directory to probe (default: the system temp dir)')
    parser.add_argument('--files', type=int, default=50)
    parser.add_argument('--seconds', type=float, default=3.0)
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        print('diagnose_windows_metadata requires Windows', file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix='artha-metadata-probe-', dir=args.dir) as scratch:
        print(json.dumps(run(Path(scratch), args.files, args.seconds), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
