"""Native, handle-relative filesystem operations for encrypted quarantine data."""
from __future__ import annotations

import ctypes
import functools
import ntpath
import os
import re
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


class QuarantineFilesystemError(RuntimeError):
    """A native quarantine filesystem operation could not be verified."""


def _last_error() -> int:
    if sys.platform != 'win32':
        raise QuarantineFilesystemError('native Windows quarantine IO is unavailable')
    else:
        return ctypes.get_last_error()


@dataclass(frozen=True)
class FileIdentity:
    volume_serial: int
    file_id: bytes


@dataclass(frozen=True)
class FileSnapshot:
    identity: FileIdentity
    size: int
    last_write_100ns: int
    change_100ns: int


class _UnicodeString(ctypes.Structure):
    _fields_ = [('Length', ctypes.c_ushort), ('MaximumLength', ctypes.c_ushort),
                ('Buffer', ctypes.c_void_p)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('Length', ctypes.c_ulong), ('RootDirectory', ctypes.c_void_p),
                ('ObjectName', ctypes.POINTER(_UnicodeString)), ('Attributes', ctypes.c_ulong),
                ('SecurityDescriptor', ctypes.c_void_p), ('SecurityQualityOfService', ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [('FileAttributes', ctypes.c_ulong), ('ReparseTag', ctypes.c_ulong)]


class _FileId128(ctypes.Structure):
    _fields_ = [('Identifier', ctypes.c_ubyte * 16)]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [('VolumeSerialNumber', ctypes.c_ulonglong), ('FileId', _FileId128)]


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [('AllocationSize', ctypes.c_longlong), ('EndOfFile', ctypes.c_longlong),
                ('NumberOfLinks', ctypes.c_ulong), ('DeletePending', ctypes.c_byte),
                ('Directory', ctypes.c_byte)]


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [('CreationTime', ctypes.c_longlong), ('LastAccessTime', ctypes.c_longlong),
                ('LastWriteTime', ctypes.c_longlong), ('ChangeTime', ctypes.c_longlong),
                ('FileAttributes', ctypes.c_ulong)]


class _Overlapped(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', ctypes.c_ulong), ('OffsetHigh', ctypes.c_ulong),
                ('hEvent', ctypes.c_void_p)]


class _FileRenameInfo(ctypes.Structure):
    _fields_ = [('ReplaceIfExists', ctypes.c_byte), ('RootDirectory', ctypes.c_void_p),
                ('FileNameLength', ctypes.c_ulong), ('FileName', ctypes.c_wchar * 1)]


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [('DeleteFile', ctypes.c_byte)]


class _AclSizeInfo(ctypes.Structure):
    _fields_ = [('AceCount', ctypes.c_ulong), ('AclBytesInUse', ctypes.c_ulong),
                ('AclBytesFree', ctypes.c_ulong)]


class _AceHeader(ctypes.Structure):
    _fields_ = [('AceType', ctypes.c_ubyte), ('AceFlags', ctypes.c_ubyte), ('AceSize', ctypes.c_ushort)]


class _Native:
    FILE_ATTRIBUTE_DIRECTORY = 0x10
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    FILE_OPEN = 1
    FILE_CREATE = 2
    FILE_DIRECTORY_FILE = 0x1
    FILE_SYNCHRONOUS_IO_NONALERT = 0x20
    FILE_NON_DIRECTORY_FILE = 0x40
    FILE_OPEN_REPARSE_POINT = 0x00200000
    FILE_SHARE_READ = 1
    FILE_SHARE_WRITE = 2
    OBJ_CASE_INSENSITIVE = 0x40
    READ_CONTROL = 0x00020000
    DELETE = 0x00010000
    FILE_READ_DATA = 0x1
    FILE_WRITE_DATA = 0x2
    FILE_READ_ATTRIBUTES = 0x80
    FILE_WRITE_ATTRIBUTES = 0x100
    SYNCHRONIZE = 0x00100000
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_ALL_ACCESS = 0x001F01FF
    SECURITY_DESCRIPTOR_CONTROL_DACL_PRESENT = 0x0004
    SECURITY_DESCRIPTOR_CONTROL_DACL_PROTECTED = 0x1000
    OWNER_SECURITY_INFORMATION = 0x1
    DACL_SECURITY_INFORMATION = 0x4
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    SE_FILE_OBJECT = 1
    ACL_SIZE_INFORMATION = 2
    ACCESS_ALLOWED_ACE_TYPE = 0
    FILE_ATTRIBUTE_TAG_INFO = 9
    FILE_STANDARD_INFO = 1
    FILE_BASIC_INFO = 0
    FILE_ID_INFO = 18
    FILE_END_OF_FILE_INFO = 6
    FILE_RENAME_INFO = 10
    FILE_DISPOSITION_INFO = 4
    LOCKFILE_EXCLUSIVE_LOCK = 0x2
    LOCKFILE_FAIL_IMMEDIATELY = 0x1
    TOKEN_QUERY = 0x8
    TOKEN_USER = 1
    ERROR_LOCK_VIOLATION = 33
    ERROR_NOT_LOCKED = 158
    STATUS_NO_MORE_FILES = 0x80000006
    kernel32: ctypes.CDLL
    advapi32: ctypes.CDLL
    ntdll: ctypes.CDLL

    def __init__(self) -> None:
        if sys.platform != 'win32' or os.name != 'nt':
            raise QuarantineFilesystemError('native Windows quarantine IO is unavailable')
        else:
            self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            self.advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
            self.ntdll = ctypes.WinDLL('ntdll')
        self._bind()

    def _bind(self) -> None:
        self._bind_ntdll()
        self._bind_kernel32()
        self._bind_advapi32()

    def _bind_ntdll(self) -> None:
        self.ntdll.NtSetSecurityObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.ntdll.NtSetSecurityObject.restype = ctypes.c_long
        self.ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(_ObjectAttributes),
            ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.ntdll.NtCreateFile.restype = ctypes.c_long
        self.ntdll.NtSetInformationFile.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD,
        ]
        self.ntdll.NtSetInformationFile.restype = ctypes.c_long
        self.ntdll.NtFlushBuffersFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock)]
        self.ntdll.NtFlushBuffersFile.restype = ctypes.c_long
        self.ntdll.NtQueryDirectoryFile.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_int, wintypes.BOOLEAN, ctypes.c_void_p, wintypes.BOOLEAN,
        ]
        self.ntdll.NtQueryDirectoryFile.restype = ctypes.c_long

    def _bind_kernel32(self) -> None:
        self.kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetDriveTypeW.restype = wintypes.UINT
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.kernel32.ReadFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        self.kernel32.WriteFile.restype = wintypes.BOOL
        self.kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        self.kernel32.FlushFileBuffers.restype = wintypes.BOOL
        self.kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD,
        ]
        self.kernel32.SetFilePointerEx.restype = wintypes.BOOL
        self.kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel32.LockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.POINTER(_Overlapped),
        ]
        self.kernel32.LockFileEx.restype = wintypes.BOOL
        self.kernel32.UnlockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(_Overlapped),
        ]
        self.kernel32.UnlockFileEx.restype = wintypes.BOOL
        self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    def _bind_advapi32(self) -> None:
        self.advapi32.GetKernelObjectSecurity.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetKernelObjectSecurity.restype = wintypes.BOOL
        self.advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetTokenInformation.restype = wintypes.BOOL
        self.advapi32.GetSecurityInfo.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.GetSecurityInfo.restype = wintypes.DWORD
        self.advapi32.GetSecurityDescriptorControl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
        self.advapi32.GetAclInformation.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
        ]
        self.advapi32.GetAclInformation.restype = wintypes.BOOL
        self.advapi32.GetAce.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.GetAce.restype = wintypes.BOOL
        self.advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.advapi32.EqualSid.restype = wintypes.BOOL
        self.advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
        self.advapi32.IsValidSid.restype = wintypes.BOOL
        self.advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
        self.advapi32.GetLengthSid.restype = wintypes.DWORD
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.advapi32.ConvertStringSidToSidW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p

    def check_nt(self, status: int, operation: str) -> None:
        if status < 0:
            raise QuarantineFilesystemError(f'{operation} was refused (NTSTATUS 0x{status & 0xffffffff:08x})')

    def check_bool(self, result: int, operation: str) -> None:
        if not result:
            error = _last_error()
            raise QuarantineFilesystemError(f'{operation} failed (Windows error {error})')

    def close(self, handle: int) -> None:
        if handle and handle != ctypes.c_void_p(-1).value:
            self.kernel32.CloseHandle(handle)

    def _unicode(self, name: str) -> tuple[_UnicodeString, ctypes.Array[ctypes.c_wchar]]:
        buffer = ctypes.create_unicode_buffer(name)
        length = len(name.encode('utf-16-le'))
        if length > 0xFFFC:
            raise QuarantineFilesystemError('native path component exceeds its bound')
        value = _UnicodeString(length, length + 2, ctypes.cast(buffer, ctypes.c_void_p))
        return value, buffer

    def create_or_open(self, parent: int | None, name: str, *, directory: bool, create: bool,
                       access: int, security_descriptor: int | None = None,
                       exclusive: bool = False) -> int:
        unicode_name, keepalive = self._unicode(name)
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(unicode_name),
            self.OBJ_CASE_INSENSITIVE, security_descriptor, None,
        )
        io_status = _IoStatusBlock()
        output = wintypes.HANDLE()
        options = self.FILE_OPEN_REPARSE_POINT | self.FILE_SYNCHRONOUS_IO_NONALERT
        options |= self.FILE_DIRECTORY_FILE if directory else self.FILE_NON_DIRECTORY_FILE
        status = self.ntdll.NtCreateFile(
            ctypes.byref(output), access | self.READ_CONTROL | self.FILE_READ_ATTRIBUTES | self.SYNCHRONIZE,
            ctypes.byref(attributes), ctypes.byref(io_status), None, 0,
            0 if exclusive else self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
            self.FILE_CREATE if create else self.FILE_OPEN, options, None, 0,
        )
        _ = keepalive
        self.check_nt(status, 'handle-relative open')
        if output.value is None:
            raise QuarantineFilesystemError('handle-relative open returned no handle')
        handle = int(output.value)
        try:
            self.verify_type(handle, directory=directory)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def verify_type(self, handle: int, *, directory: bool) -> None:
        info = _FileAttributeTagInfo()
        self.check_bool(self.kernel32.GetFileInformationByHandleEx(
            handle, self.FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info),
        ), 'file attribute inspection')
        if info.FileAttributes & self.FILE_ATTRIBUTE_REPARSE_POINT:
            raise QuarantineFilesystemError('reparse points are not permitted')
        if bool(info.FileAttributes & self.FILE_ATTRIBUTE_DIRECTORY) != directory:
            raise QuarantineFilesystemError('unexpected filesystem object type')
        standard = _FileStandardInfo()
        self.check_bool(self.kernel32.GetFileInformationByHandleEx(
            handle, self.FILE_STANDARD_INFO, ctypes.byref(standard), ctypes.sizeof(standard),
        ), 'file identity inspection')
        if standard.DeletePending or (not directory and standard.NumberOfLinks != 1):
            raise QuarantineFilesystemError('linked, pending-delete, or replaced file')

    def identity(self, handle: int) -> FileIdentity:
        info = _FileIdInfo()
        self.check_bool(self.kernel32.GetFileInformationByHandleEx(
            handle, self.FILE_ID_INFO, ctypes.byref(info), ctypes.sizeof(info),
        ), 'stable file identity inspection')
        return FileIdentity(int(info.VolumeSerialNumber), bytes(info.FileId.Identifier))

    def snapshot(self, handle: int) -> FileSnapshot:
        standard = _FileStandardInfo()
        basic = _FileBasicInfo()
        self.check_bool(self.kernel32.GetFileInformationByHandleEx(
            handle, self.FILE_STANDARD_INFO, ctypes.byref(standard), ctypes.sizeof(standard),
        ), 'file snapshot inspection')
        self.check_bool(self.kernel32.GetFileInformationByHandleEx(
            handle, self.FILE_BASIC_INFO, ctypes.byref(basic), ctypes.sizeof(basic),
        ), 'file timestamp inspection')
        if standard.DeletePending or standard.EndOfFile < 0:
            raise QuarantineFilesystemError('file is pending deletion or has invalid size')
        return FileSnapshot(self.identity(handle), int(standard.EndOfFile), int(basic.LastWriteTime),
                            int(basic.ChangeTime))

    def require_ordinary_stream(self, handle: int, *, directory: bool) -> None:
        buffer = ctypes.create_string_buffer(65536)
        # FileStreamInfo includes named streams that ordinary directory enumeration omits.
        if not self.kernel32.GetFileInformationByHandleEx(handle, 7, buffer, len(buffer)):
            if directory and _last_error() == 38:  # ERROR_HANDLE_EOF: directory has no data streams.
                return
            raise QuarantineFilesystemError('source stream inventory is unavailable')
        next_offset, name_size = struct.unpack_from('<II', buffer)
        if next_offset or name_size != 14 or buffer.raw[24:38] != '::$DATA'.encode('utf-16-le'):
            raise QuarantineFilesystemError('named source streams cannot be preserved')

    def current_user_sid(self) -> tuple[str, bytes]:
        token = wintypes.HANDLE()
        self.check_bool(self.advapi32.OpenProcessToken(
            self.kernel32.GetCurrentProcess(), self.TOKEN_QUERY, ctypes.byref(token),
        ), 'current owner lookup')
        if token.value is None:
            raise QuarantineFilesystemError('current owner token is unavailable')
        token_user: ctypes.Array[ctypes.c_char] | None = None
        sid_string = ctypes.c_void_p()
        try:
            required = wintypes.DWORD()
            self.advapi32.GetTokenInformation(token, self.TOKEN_USER, None, 0, ctypes.byref(required))
            if required.value == 0 or required.value > 65536:
                raise QuarantineFilesystemError('current owner token is unavailable')
            token_user = ctypes.create_string_buffer(required.value)
            self.check_bool(self.advapi32.GetTokenInformation(
                token, self.TOKEN_USER, token_user, required, ctypes.byref(required),
            ), 'current owner lookup')
            sid = ctypes.cast(token_user, ctypes.POINTER(ctypes.c_void_p))[0]
            if not self.advapi32.IsValidSid(sid):
                raise QuarantineFilesystemError('current owner SID is invalid')
            if not self.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
                raise QuarantineFilesystemError('current owner SID cannot be encoded')
            text = ctypes.wstring_at(sid_string)
            sid_length = self.advapi32.GetLengthSid(sid)
            if not sid_length or sid_length > 68:
                raise QuarantineFilesystemError('current owner SID length is invalid')
            return text, ctypes.string_at(sid, sid_length)
        finally:
            if sid_string.value:
                self.kernel32.LocalFree(sid_string)
            self.close(int(token.value))

    def _sid_from_string(self, value: str) -> int:
        pointer = ctypes.c_void_p()
        if not self.advapi32.ConvertStringSidToSidW(value, ctypes.byref(pointer)):
            raise QuarantineFilesystemError('security SID could not be created')
        if pointer.value is None:
            raise QuarantineFilesystemError('security SID could not be created')
        return int(pointer.value)

    def owner_private_check(self, handle: int, *, directory: bool | None = None) -> FileIdentity:
        self.verify_type(handle, directory=directory if directory is not None else self._is_directory(handle))
        security_descriptor, owner, dacl = self._security_descriptor(handle)
        try:
            self._verify_private_security(owner, dacl, security_descriptor)
            return self.identity(handle)
        finally:
            self.kernel32.LocalFree(security_descriptor)

    def owner_check(self, handle: int, *, directory: bool) -> FileIdentity:
        """Require the current-user owner and an unlinked object, without requiring a private DACL."""
        self.verify_type(handle, directory=directory)
        security_descriptor, owner, _ = self._security_descriptor(handle)
        try:
            owner_string, _ = self.current_user_sid()
            owner_sid = self._sid_from_string(owner_string)
            try:
                if not owner.value or not self.advapi32.EqualSid(owner.value, owner_sid):
                    raise QuarantineFilesystemError('filesystem object belongs to another owner')
            finally:
                self.kernel32.LocalFree(owner_sid)
            return self.identity(handle)
        finally:
            self.kernel32.LocalFree(security_descriptor)

    def _security_descriptor(self, handle: int) -> tuple[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]:
        security_descriptor = ctypes.c_void_p()
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        result = self.advapi32.GetSecurityInfo(
            handle, self.SE_FILE_OBJECT,
            self.OWNER_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION,
            ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(security_descriptor),
        )
        if result:
            raise QuarantineFilesystemError(f'owner-private security inspection failed (Windows error {result})')
        return security_descriptor, owner, dacl

    def security_bytes(self, handle: int) -> tuple[bytes, int]:
        """Read the stored owner/group/DACL, not GetSecurityInfo's parent-derived ACE flags."""
        required = wintypes.DWORD()
        information = self.OWNER_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION | 0x2
        result = self.advapi32.GetKernelObjectSecurity(handle, information, None, 0, ctypes.byref(required))
        if result or _last_error() != 122 or not 0 < required.value <= 65536:
            raise QuarantineFilesystemError('security descriptor size is unavailable or unbounded')
        buffer = ctypes.create_string_buffer(required.value)
        self.check_bool(self.advapi32.GetKernelObjectSecurity(
            handle, information, buffer, len(buffer), ctypes.byref(required),
        ), 'stored security descriptor inspection')
        if not 0 < required.value <= len(buffer):
            raise QuarantineFilesystemError('stored security descriptor changed size')
        control = ctypes.c_ushort()
        revision = ctypes.c_ulong()
        self.check_bool(self.advapi32.GetSecurityDescriptorControl(
            buffer, ctypes.byref(control), ctypes.byref(revision),
        ), 'security control inspection')
        return buffer.raw[:required.value], control.value

    def set_private_dacl(self, handle: int) -> None:
        """Set only this handle's DACL, without Win32 inheritance propagation."""
        descriptor = self.private_security_descriptor()
        try:
            self.check_nt(self.ntdll.NtSetSecurityObject(
                handle, self.DACL_SECURITY_INFORMATION | self.PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor,
            ), 'nonpropagating private DACL adoption')
        finally:
            self.kernel32.LocalFree(descriptor)

    def _verify_private_security(self, owner: ctypes.c_void_p, dacl: ctypes.c_void_p,
                                 descriptor: ctypes.c_void_p) -> None:
        control = ctypes.c_ushort()
        revision = ctypes.c_ulong()
        if not dacl.value or not owner.value or not self.advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise QuarantineFilesystemError('owner-private security descriptor is incomplete')
        required = self.SECURITY_DESCRIPTOR_CONTROL_DACL_PRESENT | self.SECURITY_DESCRIPTOR_CONTROL_DACL_PROTECTED
        if control.value & required != required:
            raise QuarantineFilesystemError('owner-private DACL must be explicit and protected')
        owner_string, _ = self.current_user_sid()
        owner_sid = self._sid_from_string(owner_string)
        owner_rights_sid = self._sid_from_string('S-1-3-4')
        system_sid = self._sid_from_string('S-1-5-18')
        try:
            if not self.advapi32.EqualSid(owner.value, owner_sid):
                raise QuarantineFilesystemError('filesystem object belongs to another owner')
            self._verify_private_dacl(dacl, owner_rights_sid, system_sid)
        finally:
            self.kernel32.LocalFree(owner_sid)
            self.kernel32.LocalFree(owner_rights_sid)
            self.kernel32.LocalFree(system_sid)

    def _verify_private_dacl(self, dacl: ctypes.c_void_p, owner_rights_sid: int, system_sid: int) -> None:
        acl = _AclSizeInfo()
        if not self.advapi32.GetAclInformation(
                dacl, ctypes.byref(acl), ctypes.sizeof(acl), self.ACL_SIZE_INFORMATION):
            raise QuarantineFilesystemError('owner-private DACL cannot be read')
        if acl.AceCount != 2:
            raise QuarantineFilesystemError('owner-private DACL contains unexpected entries')
        seen = {self._ace_principal(dacl, index, owner_rights_sid, system_sid)
                for index in range(acl.AceCount)}
        if seen != {'owner', 'system'}:
            raise QuarantineFilesystemError('owner-private DACL is incomplete')

    def _ace_principal(self, dacl: ctypes.c_void_p, index: int,
                       owner_rights_sid: int, system_sid: int) -> str:
        ace = ctypes.c_void_p()
        if not self.advapi32.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
            raise QuarantineFilesystemError('owner-private DACL contains an invalid entry')
        header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
        mask = ctypes.cast(ace.value + ctypes.sizeof(_AceHeader), ctypes.POINTER(ctypes.c_ulong))[0]
        sid = ctypes.c_void_p(ace.value + ctypes.sizeof(_AceHeader) + ctypes.sizeof(ctypes.c_ulong))
        if header.AceType != self.ACCESS_ALLOWED_ACE_TYPE or header.AceFlags or mask != self.FILE_ALL_ACCESS:
            raise QuarantineFilesystemError('owner-private DACL permissions are broader or malformed')
        if self.advapi32.EqualSid(sid, owner_rights_sid):
            return 'owner'
        if self.advapi32.EqualSid(sid, system_sid):
            return 'system'
        raise QuarantineFilesystemError('owner-private DACL grants an unexpected principal')

    def _is_directory(self, handle: int) -> bool:
        info = _FileAttributeTagInfo()
        self.check_bool(self.kernel32.GetFileInformationByHandleEx(
            handle, self.FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info),
        ), 'file attribute inspection')
        return bool(info.FileAttributes & self.FILE_ATTRIBUTE_DIRECTORY)

    def private_security_descriptor(self) -> int:
        owner, _ = self.current_user_sid()
        descriptor = ctypes.c_void_p()
        if not self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f'O:{owner}D:P(A;;FA;;;OW)(A;;FA;;;SY)', 1, ctypes.byref(descriptor), None):
            raise QuarantineFilesystemError('owner-private security descriptor could not be built')
        if descriptor.value is None:
            raise QuarantineFilesystemError('owner-private security descriptor could not be built')
        return int(descriptor.value)

    def open_volume(self, drive: str) -> int:
        return self.create_or_open(
            None, f'\\??\\{drive}\\', directory=True, create=False,
            access=self.GENERIC_READ,
        )

    def require_local_storage_drive(self, drive: str) -> None:
        """Classify a parsed drive before storage adoption resolves or opens its target."""
        if self.kernel32.GetDriveTypeW(drive + '\\') not in (2, 3, 6):
            raise QuarantineFilesystemError('storage adoption requires a verified local writable drive')


@functools.cache
def _native() -> _Native:
    return _Native()


def owner_private_handle_check(handle: int, *, directory: bool | None = None) -> FileIdentity:
    """Verify owner, protected DACL, no-reparse type, link count and stable file identity."""
    return _native().owner_private_check(handle, directory=directory)


def _native_component(name: str) -> str:
    if (not isinstance(name, str) or not name or name in {'.', '..'} or ':' in name
            or '/' in name or '\\' in name or '\x00' in name
            or name.endswith(('.', ' ')) or any(ord(char) < 32 for char in name)
            or len(name.encode('utf-16-le')) > 510):
        raise QuarantineFilesystemError('unsafe native path component')
    return name


def _absolute_components(path: Path | str) -> tuple[str, list[str], str]:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or raw.startswith(('\\\\', '//')):
        raise QuarantineFilesystemError('only absolute local-drive paths are supported')
    drive, tail = ntpath.splitdrive(raw)
    if not re.fullmatch(r'[A-Za-z]:', drive) or not tail.startswith(('\\', '/')):
        raise QuarantineFilesystemError('only absolute local-drive paths are supported')
    if any(part in {'.', '..'} for part in re.split(r'[\\/]+', tail) if part):
        raise QuarantineFilesystemError('path traversal is not permitted')
    normalized = ntpath.normpath(drive + tail)
    _, normalized_tail = ntpath.splitdrive(normalized)
    components = [part for part in re.split(r'[\\/]+', normalized_tail) if part]
    for component in components:
        _native_component(component)
    return drive[0].upper() + ':', components, normalized


def _open_directory_chain(path: Path | str, *, require_private_leaf: bool = True,
                          writable_leaf: bool = False
                          ) -> tuple[_Native, list[int], str]:
    native = _native()
    drive, components, normalized = _absolute_components(path)
    chain: list[int] = [native.open_volume(drive)]
    try:
        for index, component in enumerate(components):
            access = native.GENERIC_READ
            if index == len(components) - 1 and (require_private_leaf or writable_leaf):
                access |= native.GENERIC_WRITE
            handle = native.create_or_open(
                chain[-1], component, directory=True, create=False,
                access=access,
            )
            chain.append(handle)
            if require_private_leaf and index == len(components) - 1:
                native.owner_private_check(handle, directory=True)
        return native, chain, normalized
    except BaseException:
        for handle in reversed(chain):
            native.close(handle)
        raise


class WindowsQuarantineFile:
    def __init__(self, native: _Native, parent: WindowsQuarantineDirectory, name: str, handle: int, *,
                 private: bool = True):
        self._native = native
        self._parent = parent
        self.name = name
        self.handle = handle
        self._private = private
        self.identity = self._validate()
        self._closed = False

    def _validate(self) -> FileIdentity:
        if self._private:
            return self._native.owner_private_check(self.handle, directory=False)
        return self._native.owner_check(self.handle, directory=False)

    def __enter__(self) -> WindowsQuarantineFile:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._native.close(self.handle)
            self._closed = True

    def snapshot(self) -> FileSnapshot:
        self._ensure_open()
        result = self._native.snapshot(self.handle)
        self._recheck(result.identity)
        return result

    def read(self, max_bytes: int) -> bytes:
        self._ensure_open()
        if type(max_bytes) is not int or max_bytes < 0:
            raise QuarantineFilesystemError('invalid bounded read size')
        before = self.snapshot()
        if before.size > max_bytes:
            raise QuarantineFilesystemError('file exceeds its bounded read size')
        self.seek(0)
        chunks: list[bytes] = []
        remaining = before.size
        while remaining:
            count = min(remaining, 1024 * 1024)
            buffer = ctypes.create_string_buffer(count)
            read = ctypes.c_ulong()
            self._native.check_bool(self._native.kernel32.ReadFile(
                self.handle, buffer, count, ctypes.byref(read), None,
            ), 'handle-relative read')
            if read.value == 0:
                raise QuarantineFilesystemError('file ended during bounded read')
            chunks.append(buffer.raw[:read.value])
            remaining -= read.value
        after = self.snapshot()
        if after != before or self._recheck(before.identity) != before.identity:
            raise QuarantineFilesystemError('file changed during bounded read')
        return b''.join(chunks)

    def read_chunk(self, max_bytes: int) -> bytes:
        """Read at most ``max_bytes`` (<= 64 KiB) from the current offset; ``b''`` means end of file."""
        self._ensure_open()
        if type(max_bytes) is not int or not 0 < max_bytes <= 65536:
            raise QuarantineFilesystemError('invalid bounded chunk size')
        buffer = ctypes.create_string_buffer(max_bytes)
        read = ctypes.c_ulong()
        self._native.check_bool(self._native.kernel32.ReadFile(
            self.handle, buffer, max_bytes, ctypes.byref(read), None,
        ), 'handle-relative read')
        self._recheck(self.identity)
        return buffer.raw[:read.value]

    def seek(self, offset: int) -> None:
        self._ensure_open()
        if type(offset) is not int or offset < 0:
            raise QuarantineFilesystemError('invalid file offset')
        position = ctypes.c_longlong()
        self._native.check_bool(self._native.kernel32.SetFilePointerEx(
            self.handle, offset, ctypes.byref(position), 0,
        ), 'handle-relative seek')
        if position.value != offset:
            raise QuarantineFilesystemError('file seek was incomplete')

    def write_all(self, data: bytes) -> None:
        self._ensure_open()
        if not isinstance(data, bytes):
            raise QuarantineFilesystemError('native write requires immutable bytes')
        self._validate()
        offset = 0
        while offset < len(data):
            part = data[offset:offset + 1024 * 1024]
            buffer = ctypes.create_string_buffer(part)
            written = ctypes.c_ulong()
            self._native.check_bool(self._native.kernel32.WriteFile(
                self.handle, buffer, len(part), ctypes.byref(written), None,
            ), 'handle-relative write')
            if written.value == 0:
                raise QuarantineFilesystemError('native write made no progress')
            offset += written.value
        self._recheck(self.identity)

    def truncate(self, size: int) -> None:
        self._ensure_open()
        if type(size) is not int or size < 0:
            raise QuarantineFilesystemError('invalid file length')
        eof = ctypes.c_longlong(size)
        self._native.check_bool(self._native.kernel32.SetFileInformationByHandle(
            self.handle, self._native.FILE_END_OF_FILE_INFO, ctypes.byref(eof), ctypes.sizeof(eof),
        ), 'handle-relative truncate')
        self._recheck(self.identity)

    def flush(self) -> None:
        self._ensure_open()
        self._native.check_bool(self._native.kernel32.FlushFileBuffers(self.handle), 'file flush')
        self._recheck(self.identity)

    def _recheck(self, expected_identity: FileIdentity) -> FileIdentity:
        # Handles are opened without FILE_SHARE_DELETE, so while this file and its parent chain
        # stay open their names cannot be renamed or deleted; recheck mutable security state only.
        self._ensure_open()
        self._parent._check()
        current = self._validate()
        if current != expected_identity:
            raise QuarantineFilesystemError('open file identity changed')
        return current

    def check_path(self, expected_identity: FileIdentity | None = None) -> FileIdentity:
        """Fully revalidate the parent chain from the volume root and this file's parent-relative name."""
        self._ensure_open()
        self._parent._revalidate()
        current = self._validate()
        if expected_identity is not None and current != expected_identity:
            raise QuarantineFilesystemError('open file identity changed')
        with self._parent.open_file(self.name, writable=False, private=self._private) as path_handle:
            if path_handle.identity != current:
                raise QuarantineFilesystemError('parent-relative file identity changed')
        return current

    @contextmanager
    def lock(self) -> Iterator[None]:
        self._ensure_open()
        overlapped = _Overlapped()
        length = 0xFFFFFFFF
        result = self._native.kernel32.LockFileEx(
            self.handle, self._native.LOCKFILE_EXCLUSIVE_LOCK | self._native.LOCKFILE_FAIL_IMMEDIATELY,
            0, length, length, ctypes.byref(overlapped),
        )
        if not result:
            error = _last_error()
            if error in {self._native.ERROR_LOCK_VIOLATION, self._native.ERROR_NOT_LOCKED}:
                raise QuarantineFilesystemError('file writer lock is already held')
            raise QuarantineFilesystemError(f'file writer lock failed (Windows error {error})')
        try:
            self.check_path(expected_identity=self.identity)
            yield
            self.check_path(expected_identity=self.identity)
        finally:
            if not self._native.kernel32.UnlockFileEx(
                    self.handle, 0, length, length, ctypes.byref(overlapped)):
                raise QuarantineFilesystemError('file writer lock release failed')

    def _ensure_open(self) -> None:
        if self._closed:
            raise QuarantineFilesystemError('native file handle is closed')


class WindowsQuarantineDirectory:
    def __init__(self, native: _Native, handles: list[int], path: str, *,
                 owner_private: bool = True, initialize_lock: bool = True):
        self._native = native
        self._handles = handles
        self.path = Path(path)
        self.handle = handles[-1]
        self._owner_private = owner_private
        self.identity = (native.owner_private_check(self.handle, directory=True)
                         if owner_private else native.identity(self.handle))
        self._identities = [native.identity(handle) for handle in handles]
        self._closed = False
        if initialize_lock:
            self._ensure_lock_file()

    @classmethod
    def open(cls, path: Path | str, *, owner_private: bool = True, writable: bool = False
             ) -> WindowsQuarantineDirectory:
        """Open a directory chain; ``writable`` is needed to publish into a non-private parent."""
        native, handles, normalized = _open_directory_chain(path, require_private_leaf=owner_private,
                                                            writable_leaf=writable)
        try:
            return cls(native, handles, normalized, owner_private=owner_private,
                       initialize_lock=False)
        except BaseException:
            for handle in reversed(handles):
                native.close(handle)
            raise

    @classmethod
    def create(cls, path: Path | str, *, lock_file: bool = True) -> WindowsQuarantineDirectory:
        native = _native()
        _, components, normalized = _absolute_components(path)
        if not components:
            raise QuarantineFilesystemError('a quarantine directory cannot be a volume root')
        parent_path = ntpath.dirname(normalized)
        parent_native, handles, _ = _open_directory_chain(
            parent_path, require_private_leaf=False, writable_leaf=True,
        )
        try:
            if parent_native.__class__ is not native.__class__:
                raise QuarantineFilesystemError('native filesystem binding changed')
            descriptor = native.private_security_descriptor()
            try:
                final = native.create_or_open(
                    handles[-1], components[-1], directory=True, create=True,
                    access=native.GENERIC_READ | native.GENERIC_WRITE,
                    security_descriptor=descriptor,
                )
            finally:
                native.kernel32.LocalFree(descriptor)
            handles.append(final)
            return cls(native, handles, normalized, initialize_lock=lock_file)
        except BaseException:
            for handle in reversed(handles):
                native.close(handle)
            raise

    def __enter__(self) -> WindowsQuarantineDirectory:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            for handle in reversed(self._handles):
                self._native.close(handle)
            self._closed = True

    def create_file(self, name: str) -> WindowsQuarantineFile:
        self._ensure_open()
        name = _native_component(name)
        self._check()
        descriptor = self._native.private_security_descriptor()
        try:
            handle = self._native.create_or_open(
                self.handle, name, directory=False, create=True,
                access=self._native.GENERIC_READ | self._native.GENERIC_WRITE,
                security_descriptor=descriptor,
            )
        finally:
            self._native.kernel32.LocalFree(descriptor)
        try:
            opened = WindowsQuarantineFile(self._native, self, name, handle)
            self._check()
            return opened
        except BaseException:
            self._native.close(handle)
            raise

    def open_file(self, name: str, *, writable: bool = False, rename: bool = False,
                  private: bool = True) -> WindowsQuarantineFile:
        self._ensure_open()
        name = _native_component(name)
        if rename:
            self._revalidate()
        access = self._native.GENERIC_READ
        if writable:
            access |= self._native.GENERIC_WRITE
        if rename:
            access |= self._native.DELETE
        handle = self._native.create_or_open(
            self.handle, name, directory=False, create=False, access=access,
        )
        try:
            opened = WindowsQuarantineFile(self._native, self, name, handle, private=private)
            if not rename:
                self._check()
            return opened
        except BaseException:
            self._native.close(handle)
            raise

    def read_file(self, name: str, max_bytes: int, *,
                  expected_identity: FileIdentity | None = None, private: bool = True) -> bytes:
        with self.open_file(name, private=private) as opened:
            if expected_identity is not None and opened.identity != expected_identity:
                raise QuarantineFilesystemError('file identity differs from the pinned identity')
            return opened.read(max_bytes)

    def publish_write_once(self, source: str, destination: str, *,
                           expected_identity: FileIdentity) -> FileIdentity:
        return self._rename(source, destination, expected_identity=expected_identity, replaced=None)

    def replace_file(self, source: str, destination: str, *, expected_identity: FileIdentity,
                     replaced_identity: FileIdentity) -> FileIdentity:
        """Atomically replace one pinned owner-private file with one pinned staged file."""
        return self._rename(source, destination, expected_identity=expected_identity,
                            replaced=replaced_identity)

    def _rename(self, source: str, destination: str, *, expected_identity: FileIdentity,
                replaced: FileIdentity | None) -> FileIdentity:
        source = _native_component(source)
        destination = _native_component(destination)
        if source == destination:
            raise QuarantineFilesystemError('publication source and destination must differ')
        # Fail before publication when the directory handle cannot make the rename durable.
        self._flush_directory()
        with self.open_file(source, writable=True) as staged:
            if staged.identity != expected_identity:
                raise QuarantineFilesystemError('staging file identity changed')
            staged.flush()
        if replaced is not None:
            with self.open_file(destination) as current:
                if current.identity != replaced:
                    raise QuarantineFilesystemError('replacement target identity changed')
        with self.open_file(source, writable=True, rename=True) as staged:
            if staged.identity != expected_identity:
                raise QuarantineFilesystemError('staging file identity changed')
            encoded = destination.encode('utf-16-le')
            offset = _FileRenameInfo.FileName.offset
            info_buffer = ctypes.create_string_buffer(offset + len(encoded))
            info = ctypes.cast(info_buffer, ctypes.POINTER(_FileRenameInfo)).contents
            info.ReplaceIfExists = int(replaced is not None)
            info.RootDirectory = self.handle
            info.FileNameLength = len(encoded)
            ctypes.memmove(ctypes.addressof(info) + offset, encoded, len(encoded))
            io_status = _IoStatusBlock()
            status = self._native.ntdll.NtSetInformationFile(
                staged.handle, ctypes.byref(io_status), info_buffer, len(info_buffer),
                self._native.FILE_RENAME_INFO,
            )
            self._native.check_nt(status, 'write-once handle-relative publication')
        with self.open_file(destination, writable=True) as published:
            if published.identity != expected_identity:
                raise QuarantineFilesystemError('published file identity changed')
            published.flush()
            self._flush_directory()
            self._revalidate()
            return expected_identity

    def list_files(self, *, validate: bool = True) -> list[str]:
        self._ensure_open()
        self._check()

        class DirectoryInformation(ctypes.Structure):
            _fields_ = [
                ('NextEntryOffset', wintypes.DWORD), ('FileIndex', wintypes.DWORD),
                ('CreationTime', ctypes.c_longlong), ('LastAccessTime', ctypes.c_longlong),
                ('LastWriteTime', ctypes.c_longlong), ('ChangeTime', ctypes.c_longlong),
                ('EndOfFile', ctypes.c_longlong), ('AllocationSize', ctypes.c_longlong),
                ('FileAttributes', wintypes.DWORD), ('FileNameLength', wintypes.DWORD),
                ('FileName', ctypes.c_wchar * 1),
            ]

        buffer = ctypes.create_string_buffer(65536)
        io_status = _IoStatusBlock()
        names: list[str] = []
        restart = True
        while True:
            status = self._native.ntdll.NtQueryDirectoryFile(
                self.handle, None, None, None, ctypes.byref(io_status), buffer, len(buffer),
                1, 0, None, restart,
            )
            restart = False
            code = status & 0xFFFFFFFF
            if code == self._native.STATUS_NO_MORE_FILES:
                break
            self._native.check_nt(status, 'handle-relative directory enumeration')
            offset = 0
            while True:
                info = ctypes.cast(ctypes.addressof(buffer) + offset,
                                   ctypes.POINTER(DirectoryInformation)).contents
                if info.FileNameLength % 2:
                    raise QuarantineFilesystemError('directory entry has an invalid name')
                name = ctypes.wstring_at(ctypes.addressof(buffer) + offset + DirectoryInformation.FileName.offset,
                                         info.FileNameLength // 2)
                if name not in {'.', '..'}:
                    _native_component(name)
                    names.append(name)
                if not info.NextEntryOffset:
                    break
                offset += info.NextEntryOffset
        self._check()
        if validate:
            for name in names:
                with self.open_file(name):
                    pass
        return sorted(names)

    def snapshot(self) -> FileSnapshot:
        self._ensure_open()
        self._check()
        identity = (self._native.owner_private_check(self.handle, directory=True)
                    if self._owner_private else self._native.identity(self.handle))
        if identity != self.identity:
            raise QuarantineFilesystemError('directory identity changed')
        result = self._native.snapshot(self.handle)
        self._check()
        return result

    def open_directory(self, name: str, *, owner_private: bool = True
                       ) -> WindowsQuarantineDirectory:
        self._ensure_open()
        name = _native_component(name)
        self._check()
        handle = self._native.create_or_open(
            self.handle, name, directory=True, create=False,
            access=self._native.GENERIC_READ | self._native.GENERIC_WRITE,
        )
        path = ntpath.join(str(self.path), name)
        try:
            native, handles, normalized = _open_directory_chain(
                path, require_private_leaf=owner_private, writable_leaf=True,
            )
            try:
                if native.identity(handles[-1]) != self._native.identity(handle):
                    raise QuarantineFilesystemError('parent-relative directory identity changed')
                return WindowsQuarantineDirectory(
                    native, handles, normalized,
                    owner_private=owner_private, initialize_lock=False,
                )
            except BaseException:
                for item in reversed(handles):
                    native.close(item)
                raise
        finally:
            self._native.close(handle)

    def delete_file(self, name: str, *, expected_identity: FileIdentity) -> None:
        """Delete one identity-pinned, current-owner file even if its private DACL was altered."""
        name = _native_component(name)
        with self.open_file(name, rename=True, private=False) as opened:
            if opened.identity != expected_identity:
                raise QuarantineFilesystemError('refusing to delete a substituted file')
            disposition = _FileDispositionInfo(1)
            self._native.check_bool(self._native.kernel32.SetFileInformationByHandle(
                opened.handle, self._native.FILE_DISPOSITION_INFO,
                ctypes.byref(disposition), ctypes.sizeof(disposition),
            ), 'handle-relative file cleanup')
            self._flush_directory()
        self._revalidate()

    @contextmanager
    def lock(self) -> Iterator[None]:
        self._ensure_open()
        try:
            opened = self.open_file('.lock', writable=True)
        except QuarantineFilesystemError:
            self._ensure_lock_file()
            opened = self.open_file('.lock', writable=True)
        with opened as locked:
            overlapped = _Overlapped()
            result = self._native.kernel32.LockFileEx(
                locked.handle, self._native.LOCKFILE_EXCLUSIVE_LOCK
                | self._native.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, ctypes.byref(overlapped),
            )
            if not result:
                error = _last_error()
                if error in {self._native.ERROR_LOCK_VIOLATION, self._native.ERROR_NOT_LOCKED}:
                    raise QuarantineFilesystemError('quarantine writer lock is already held')
                raise QuarantineFilesystemError(f'quarantine writer lock failed (Windows error {error})')
            try:
                locked.check_path(expected_identity=locked.identity)
                yield
                locked._recheck(locked.identity)
            finally:
                if not self._native.kernel32.UnlockFileEx(
                        locked.handle, 0, 1, 0, ctypes.byref(overlapped)):
                    raise QuarantineFilesystemError('quarantine writer lock release failed')

    def _ensure_lock_file(self) -> None:
        try:
            created = self.create_file('.lock')
            with created:
                created.flush()
        except QuarantineFilesystemError:
            with self.open_file('.lock'):
                pass

    def flush(self) -> None:
        self._ensure_open()
        self._flush_directory()
        self._revalidate()

    def _flush_directory(self) -> None:
        io_status = _IoStatusBlock()
        self._native.check_nt(
            self._native.ntdll.NtFlushBuffersFile(self.handle, ctypes.byref(io_status)),
            'directory metadata flush',
        )

    def _check(self) -> None:
        self._ensure_open()
        identity = (self._native.owner_private_check(self.handle, directory=True)
                    if self._owner_private else self._native.identity(self.handle))
        if identity != self.identity:
            raise QuarantineFilesystemError('quarantine directory identity changed')

    def _revalidate(self) -> None:
        self._check()
        native, opened, _ = _open_directory_chain(self.path, require_private_leaf=self._owner_private)
        try:
            current = [native.identity(handle) for handle in opened]
            if current != self._identities:
                raise QuarantineFilesystemError('quarantine ancestry identity changed')
        finally:
            for handle in reversed(opened):
                native.close(handle)

    def _ensure_open(self) -> None:
        if self._closed:
            raise QuarantineFilesystemError('native directory handle is closed')


@contextmanager
def open_owner_private_path(path: Path | str, *, writable: bool = False) -> Iterator[WindowsQuarantineFile]:
    value = Path(path)
    with (WindowsQuarantineDirectory.open(value.parent, owner_private=False) as parent,
          parent.open_file(value.name, writable=writable) as opened):
        yield opened


def require_ordinary_stream(path: Path, *, directory: bool) -> None:
    """Refuse unrepresented source streams using a checked, stable native handle."""
    if directory:
        with WindowsQuarantineDirectory.open(path, owner_private=False) as opened:
            before = opened.snapshot()
            opened._native.require_ordinary_stream(opened.handle, directory=True)
            if opened.snapshot() != before:
                raise QuarantineFilesystemError('source changed during stream inventory')
    else:
        with (WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent,
              parent.open_file(path.name, private=False) as member):
            before = member.snapshot()
            member._native.require_ordinary_stream(member.handle, directory=False)
            if member.snapshot() != before:
                raise QuarantineFilesystemError('source changed during stream inventory')


def ensure_private_directory(path: Path) -> None:
    """Create missing native ancestors privately; never repair an unsafe existing leaf."""
    missing = []
    directory = path
    while not directory.exists():
        if directory.parent == directory:
            raise QuarantineFilesystemError('directory volume is unavailable')
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        try:
            with WindowsQuarantineDirectory.create(directory, lock_file=False):
                pass
        except QuarantineFilesystemError:
            with WindowsQuarantineDirectory.open(directory):
                pass
    with WindowsQuarantineDirectory.open(path):
        pass
