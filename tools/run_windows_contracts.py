#!/usr/bin/env python3
"""Launch isolated Windows contracts with a user-owned producer token.

Elevated hosted runners default to Administrators-owned files. Only a duplicate
of the runner token is adjusted here; production owner checks stay unchanged.
This changes neither elevation nor privileges and is not a security sandbox.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
        ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
        ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
                ("process_id", w.DWORD), ("thread_id", w.DWORD)]


class _Native:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("the Windows contract launcher requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        signatures = [
            (self.kernel.GetCurrentProcess, [], w.HANDLE),
            (self.kernel.CloseHandle, [w.HANDLE], w.BOOL),
            (self.kernel.LocalFree, [ctypes.c_void_p], ctypes.c_void_p),
            (self.kernel.WaitForSingleObject, [w.HANDLE, w.DWORD], w.DWORD),
            (self.kernel.GetExitCodeProcess, [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            (self.security.OpenProcessToken, [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
            (self.security.GetTokenInformation,
             [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            (self.security.SetTokenInformation,
             [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            (self.security.DuplicateTokenEx,
             [w.HANDLE, w.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
              ctypes.POINTER(w.HANDLE)], w.BOOL),
            (self.security.EqualSid, [ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
            (self.security.GetNamedSecurityInfoW,
             [w.LPCWSTR, ctypes.c_int, w.DWORD, ctypes.POINTER(ctypes.c_void_p),
              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
              ctypes.POINTER(ctypes.c_void_p)], w.DWORD),
            (self.security.CreateProcessAsUserW,
             [w.HANDLE, w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
              ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInfo)],
             w.BOOL),
        ]
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result

    @staticmethod
    def check(result, call="Windows API call"):
        if not result:
            error = ctypes.WinError(ctypes.get_last_error())
            raise OSError(error.errno, f"{call} refused: {error.strerror}", None, error.winerror)

    @contextmanager
    def process_token(self):
        token = w.HANDLE()
        # TOKEN_QUERY | TOKEN_DUPLICATE; the caller's token is never adjusted.
        self.check(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 0xA,
                                                 ctypes.byref(token)), "OpenProcessToken")
        try:
            yield token
        finally:
            self.check(self.kernel.CloseHandle(token))

    def information(self, token, kind):
        size = w.DWORD()
        result = self.security.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
        if result or ctypes.get_last_error() != 122 or not size.value:
            raise OSError("could not size Windows token information")
        buffer = ctypes.create_string_buffer(size.value)
        self.check(self.security.GetTokenInformation(
            token, kind, buffer, size.value, ctypes.byref(size)))
        return buffer

    @staticmethod
    def sid(information):
        # TOKEN_USER and TOKEN_OWNER both start with a PSID.
        return ctypes.cast(information, ctypes.POINTER(ctypes.c_void_p))[0]

    def same_sid(self, first, second):
        return bool(self.security.EqualSid(self.sid(first), self.sid(second)))

    @contextmanager
    def duplicate_for_tests(self, original):
        token = w.HANDLE()
        # TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT.
        self.check(self.security.DuplicateTokenEx(
            original, 0x8B, None, 2, 1, ctypes.byref(token)), "DuplicateTokenEx")
        try:
            user = self.information(token, 1)
            owner = ctypes.c_void_p(self.sid(user))
            self.check(self.security.SetTokenInformation(
                token, 4, ctypes.byref(owner), ctypes.sizeof(owner)), "SetTokenInformation(TokenOwner)")
            if not self.same_sid(user, self.information(token, 4)):
                raise OSError("duplicated token default owner is not its user")
            yield token
        finally:
            self.check(self.kernel.CloseHandle(token))

    def launch(self, argv):
        process = _ProcessInfo()
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        with self.process_token() as original, self.duplicate_for_tests(original) as token:
            # A duplicate of the caller's own token needs no extra privilege and no Secondary
            # Logon service (blocked on hosted runners). Never enable a privilege or fall back
            # to another token when this call is refused.
            self.check(self.security.CreateProcessAsUserW(
                token, sys.executable, command, None, None, False, 0, None, None,
                ctypes.byref(startup), ctypes.byref(process)), "CreateProcessAsUserW")
        try:
            if self.kernel.WaitForSingleObject(process.process, 0xFFFFFFFF) != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            code = w.DWORD()
            self.check(self.kernel.GetExitCodeProcess(process.process, ctypes.byref(code)))
            return code.value
        finally:
            self.check(self.kernel.CloseHandle(process.thread))
            self.check(self.kernel.CloseHandle(process.process))

    def verify_owner(self, path, user):
        owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
        code = self.security.GetNamedSecurityInfoW(
            str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        try:
            if not self.security.EqualSid(owner, self.sid(user)):
                raise OSError("a fresh test producer artifact is not owned by its user")
        finally:
            self.kernel.LocalFree(descriptor)

    def verify_producers(self):
        with self.process_token() as token:
            user = self.information(token, 1)
            if not self.same_sid(user, self.information(token, 4)):
                raise OSError("test child default owner is not its user")
            with tempfile.TemporaryDirectory(prefix="artha-owner-probe-") as directory:
                root = Path(directory)
                probe = root / "probe"
                probe.write_bytes(b"synthetic owner probe\n")
                self.verify_owner(root, user)
                self.verify_owner(probe, user)
                child_probe = root / "child-probe"
                subprocess.run(
                    [sys.executable, "-c",
                     "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'probe')",
                     str(child_probe)], check=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=30)
                self.verify_owner(child_probe, user)


def _child(output: Path, args: list[str]) -> int:
    with output.open("x", encoding="utf-8") as stream:
        try:
            _Native().verify_producers()
            print("Windows file, directory and subprocess ownership verified; policy unchanged.",
                  file=stream, flush=True)
            return subprocess.run(
                [sys.executable, str(ROOT / "tools" / "run_tests_isolated.py"), *args],
                stdout=stream, stderr=stream, check=False).returncode
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"Windows contract environment refused: {error}", file=stream, flush=True)
            return 2


def main(args: list[str]) -> int:
    try:
        if args[:1] == ["--child-output"]:
            if len(args) < 2:
                raise ValueError("missing child output path")
            return _child(Path(args[1]), args[2:])
        with tempfile.TemporaryDirectory(prefix="artha-windows-contracts-") as directory:
            output = Path(directory) / "tests.log"
            code = _Native().launch(
                [sys.executable, str(Path(__file__).resolve()), "--child-output", str(output), *args])
            if not output.is_file():
                raise OSError("Windows contract child output was not produced")
            print(output.read_text(encoding="utf-8"), end="")
            return code
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Windows contract environment refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
