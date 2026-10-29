"""Typed Win32 desktop handles for the local desktop control panel.

This module never injects keyboard/mouse input, moves trader windows, changes
system settings, or terminates applications. A process must be started on its
target desktop; an existing login session cannot be moved between desktops.
"""

import ctypes
import os
import re
import subprocess
from ctypes import wintypes as wt
from dataclasses import dataclass

DESKTOP_ACCESS = 0x0001 | 0x0002 | 0x0004 | 0x0040 | 0x0080 | 0x0100
UOI_NAME = 2
STILL_ACTIVE = 259
TH32CS_SNAPPROCESS = 2
ERROR_ALREADY_EXISTS = 183
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wt.BOOL, wt.HWND, wt.LPARAM
)


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


def validate_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("Invalid desktop name")
    return name


@dataclass
class Desktop:
    api: object
    handle: int
    name: str

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, 0
            self.api.user.CloseDesktop(handle)


@dataclass
class ProcessHandle:
    api: object
    handle: int
    pid: int

    def alive(self):
        code = wt.DWORD()
        return bool(
            self.handle
            and self.api.kernel.GetExitCodeProcess(
                self.handle, ctypes.byref(code)
            )
            and code.value == STILL_ACTIVE
        )

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, 0
            self.api.kernel.CloseHandle(handle)

    def wait(self, timeout_ms=1000):
        return self.api.kernel.WaitForSingleObject(
            self.handle, timeout_ms
        ) == 0


class ChildProcess(ProcessHandle):
    def terminate_helper(self):
        """Only call for the return-panel child created by this controller."""
        if self.alive():
            self.api.kernel.TerminateProcess(self.handle, 1)


class DesktopAPI:
    def __init__(self):
        if os.name != "nt":
            raise OSError("This feature requires Windows")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()

    @staticmethod
    def _signature(dll, name, result, *args):
        fn = getattr(dll, name)
        fn.argtypes = list(args)
        fn.restype = result

    def _bind(self):
        u, k = self.user, self.kernel
        bind = self._signature
        bind(
            u,
            "OpenDesktopW",
            wt.HANDLE,
            wt.LPCWSTR,
            wt.DWORD,
            wt.BOOL,
            wt.DWORD,
        )
        bind(
            u,
            "CreateDesktopW",
            wt.HANDLE,
            wt.LPCWSTR,
            wt.LPCWSTR,
            wt.LPVOID,
            wt.DWORD,
            wt.DWORD,
            wt.LPVOID,
        )
        bind(u, "CloseDesktop", wt.BOOL, wt.HANDLE)
        bind(u, "SwitchDesktop", wt.BOOL, wt.HANDLE)
        bind(u, "GetThreadDesktop", wt.HANDLE, wt.DWORD)
        bind(u, "SetThreadDesktop", wt.BOOL, wt.HANDLE)
        bind(u, "GetProcessWindowStation", wt.HANDLE)
        bind(u, "OpenInputDesktop", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
        bind(
            u,
            "GetUserObjectInformationW",
            wt.BOOL,
            wt.HANDLE,
            ctypes.c_int,
            wt.LPVOID,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
        )
        bind(u, "EnumDesktopWindows", wt.BOOL, wt.HANDLE, CALLBACK, wt.LPARAM)
        bind(
            u, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int
        )
        bind(
            u,
            "GetWindowThreadProcessId",
            wt.DWORD,
            wt.HWND,
            ctypes.POINTER(wt.DWORD),
        )
        bind(u, "IsWindow", wt.BOOL, wt.HWND)
        bind(u, "GetForegroundWindow", wt.HWND)
        bind(k, "GetCurrentThreadId", wt.DWORD)
        bind(k, "CloseHandle", wt.BOOL, wt.HANDLE)
        bind(k, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
        bind(k, "WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
        bind(k, "CreateMutexW", wt.HANDLE, wt.LPVOID, wt.BOOL, wt.LPCWSTR)
        bind(
            k,
            "CreateProcessW",
            wt.BOOL,
            wt.LPCWSTR,
            wt.LPWSTR,
            wt.LPVOID,
            wt.LPVOID,
            wt.BOOL,
            wt.DWORD,
            wt.LPVOID,
            wt.LPCWSTR,
            ctypes.POINTER(STARTUPINFO),
            ctypes.POINTER(PROCESS_INFORMATION),
        )
        bind(
            k,
            "GetExitCodeProcess",
            wt.BOOL,
            wt.HANDLE,
            ctypes.POINTER(wt.DWORD),
        )
        bind(k, "TerminateProcess", wt.BOOL, wt.HANDLE, wt.UINT)
        bind(k, "CreateToolhelp32Snapshot", wt.HANDLE, wt.DWORD, wt.DWORD)
        bind(
            k,
            "Process32FirstW",
            wt.BOOL,
            wt.HANDLE,
            ctypes.POINTER(PROCESSENTRY32),
        )
        bind(
            k,
            "Process32NextW",
            wt.BOOL,
            wt.HANDLE,
            ctypes.POINTER(PROCESSENTRY32),
        )

    @staticmethod
    def _error(action):
        code = ctypes.get_last_error()
        return OSError(
            code,
            "%s: %s"
            % (action, ctypes.FormatError(code) if code else "Win32 refused"),
        )

    def object_name(self, handle):
        needed = wt.DWORD()
        self.user.GetUserObjectInformationW(
            handle, UOI_NAME, None, 0, ctypes.byref(needed)
        )
        if not needed.value:
            raise self._error("Read desktop name")
        size = needed.value // ctypes.sizeof(wt.WCHAR) + 1
        buffer = ctypes.create_unicode_buffer(size)
        if not self.user.GetUserObjectInformationW(
            handle,
            UOI_NAME,
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(needed),
        ):
            raise self._error("Read desktop name")
        return buffer.value

    def thread_name(self):
        # GetThreadDesktop returns a borrowed handle; never close it.
        return self.object_name(
            self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
        )

    def station_name(self):
        return self.object_name(self.user.GetProcessWindowStation())

    def input_name(self):
        handle = self.user.OpenInputDesktop(0, False, 0x0001)
        if not handle:
            # A locked/UAC desktop can be inaccessible. It is NOT "Default".
            return None
        try:
            return self.object_name(handle)
        finally:
            self.user.CloseDesktop(handle)

    def open(self, name, create=False):
        validate_name(name)
        handle = self.user.OpenDesktopW(name, 0, False, DESKTOP_ACCESS)
        if not handle and create:
            code = ctypes.get_last_error()
            if code != 2:  # Only create when absent, never mask access denial.
                raise self._error("Open desktop")
            original = self.user.GetThreadDesktop(
                self.kernel.GetCurrentThreadId()
            )
            handle = self.user.CreateDesktopW(
                name, None, None, 0, DESKTOP_ACCESS, None
            )
            if handle and self.thread_name() != self.object_name(original):
                if not self.user.SetThreadDesktop(original):
                    self.user.CloseDesktop(handle)
                    raise self._error("Restore calling thread desktop")
        if not handle:
            raise self._error("Open/create desktop")
        return Desktop(self, handle, name)

    def switch(self, desktop):
        ctypes.set_last_error(0)
        if not self.user.SwitchDesktop(desktop.handle):
            raise self._error("Switch desktop (unlock Windows first)")

    def windows(self, desktop):
        result = []

        def collect(hwnd, _):
            title = ctypes.create_unicode_buffer(512)
            pid = wt.DWORD()
            self.user.GetWindowTextW(hwnd, title, len(title))
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            result.append(
                {
                    "hwnd": int(hwnd),
                    "pid": pid.value,
                    "title": title.value,
                }
            )
            return True

        callback = CALLBACK(collect)
        ctypes.set_last_error(0)
        ok = self.user.EnumDesktopWindows(desktop.handle, callback, 0)
        if not ok and ctypes.get_last_error():
            raise self._error("Enumerate desktop")
        return result

    def trader_processes(self):
        snapshot = self.kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            raise self._error("List processes")
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            present = self.kernel.Process32FirstW(
                snapshot, ctypes.byref(entry)
            )
            result = []
            while present:
                if entry.szExeFile.lower() in (
                    "happ.exe",
                    "xiadan.exe",
                    "xiadan-plus.exe",
                ):
                    result.append((entry.th32ProcessID, entry.szExeFile))
                present = self.kernel.Process32NextW(
                    snapshot, ctypes.byref(entry)
                )
            return result
        finally:
            self.kernel.CloseHandle(snapshot)

    def singleton(self):
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(
            None, False, "Local\\FrytraderDesktopPanel"
        )
        if not handle:
            raise self._error("Create controller mutex")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.kernel.CloseHandle(handle)
            raise RuntimeError("控制面板已经打开，请从任务栏找到它。")
        return handle

    def start(self, argv, desktop, cwd=None):
        startup = STARTUPINFO()
        startup.cb = ctypes.sizeof(startup)
        startup.lpDesktop = "%s\\%s" % (self.station_name(), desktop.name)
        info = PROCESS_INFORMATION()
        command = ctypes.create_unicode_buffer(
            subprocess.list2cmdline([str(arg) for arg in argv])
        )
        ctypes.set_last_error(0)
        if not self.kernel.CreateProcessW(
            str(argv[0]),
            command,
            None,
            None,
            False,
            0,
            None,
            str(cwd) if cwd else None,
            ctypes.byref(startup),
            ctypes.byref(info),
        ):
            raise self._error("Start process on desktop")
        self.kernel.CloseHandle(info.hThread)
        return ChildProcess(self, int(info.hProcess), info.dwProcessId)


def return_to_origin(api, origin, target_name):
    """Never switch away from a lock screen/UAC or an unrelated desktop."""
    current = api.input_name()
    if current == origin.name:
        return True
    if current != target_name:
        raise RuntimeError(
            "当前是锁屏、安全界面或其他桌面，请先解锁 Windows。"
        )
    api.switch(origin)
    return True
