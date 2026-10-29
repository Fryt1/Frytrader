"""Bounded thread-specific activation veto for the x86 Voyager UI thread.

The helper and hook are built from the included native sources. They never
patch executable files or send input; Windows removes the hook if the host
exits. Each query owns its helper and explicitly stops it in finally.
"""

import hashlib
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from easytrader.background_runtime import BackgroundError

_HASHES = {
    "focus_guard32.dll": (
        "6f9143a13d75c0e2b874a6854bb1bede94f0408bae7e4dff086941e88481236b"
    ),
    "focus_host32.exe": (
        "5753bae4146a96b394f4981858e2d425330595c23b236b42bc0c90e19eb49f24"
    ),
}


def pe_machine(path):
    """Reject a bitness mismatch before a window is moved or hooked."""
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise BackgroundError("原生保护文件不是 PE 可执行文件")
        stream.seek(0x3C)
        offset_bytes = stream.read(4)
        if len(offset_bytes) != 4:
            raise BackgroundError("PE 文件头不完整")
        offset = struct.unpack("<I", offset_bytes)[0]
        if not 64 <= offset <= 16 * 1024 * 1024:
            raise BackgroundError("PE 文件头位置无效")
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise BackgroundError("PE 文件签名无效")
        machine = stream.read(2)
        if len(machine) != 2:
            raise BackgroundError("PE 架构信息缺失")
        return struct.unpack("<H", machine)[0]


class ThreadActivationGuard:
    def __init__(self, directory=None):
        self.directory = (
            Path(directory)
            if directory
            else Path(__file__).resolve().parent / "native" / "bin"
        )
        for name, expected in _HASHES.items():
            path = self.directory / name
            if not path.is_file():
                raise BackgroundError("缺少原生防激活保护，请重新安装完整包")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise BackgroundError("原生防激活保护校验失败，请重新安装")
            if pe_machine(path) != 0x14C:
                raise BackgroundError("当前防激活保护仅支持 x86 下单窗口")
        self.host = None
        self.temp = None
        self.log = None
        self.folder = None

    def start(self, backend, guard):
        if self.host is not None:
            raise BackgroundError("原生防激活保护已在运行")
        guard.check()
        hwnd, pid, thread = backend.hook_target()
        self.temp = tempfile.TemporaryDirectory(prefix="frytrader-focus-")
        self.folder = Path(self.temp.name)
        self.log = (self.folder / "host.log").open("wb")
        (self.folder / "target.txt").write_text(
            "%d,0,%d,%d,%d" % (hwnd, pid, thread, os.getpid()),
            encoding="ascii",
        )
        environment = os.environ.copy()
        environment["FRYTRADER_FOCUS_DIRECTORY"] = str(self.folder)
        environment["FRYTRADER_FOCUS_DLL"] = str(
            self.directory / "focus_guard32.dll"
        )
        try:
            self.host = subprocess.Popen(
                [str(self.directory / "focus_host32.exe")],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=self.log,
                stderr=subprocess.STDOUT,
                shell=False,
            )
            deadline = min(time.monotonic() + 2, guard.deadline)
            ready = self.folder / "ready.txt"
            while not ready.exists():
                guard.check()
                if self.host.poll() is not None:
                    raise BackgroundError(
                        "防激活宿主未就绪（exit=%s）" % self.host.returncode
                    )
                if time.monotonic() >= deadline:
                    raise BackgroundError("防激活宿主启动超时")
                time.sleep(0.01)
            if ready.read_text(encoding="ascii") != "ok":
                raise BackgroundError("防激活宿主就绪信号无效")
            self.check()
            guard.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.host is None or self.host.poll() is not None:
            raise BackgroundError("防激活保护已退出，已停止后台操作")

    def close(self):
        failed = None
        try:
            if self.host is not None:
                if self.host.poll() is None:
                    (self.folder / "stop.txt").write_text(
                        "stop", encoding="ascii"
                    )
                    try:
                        self.host.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        # Only our own helper is terminated.
                        self.host.terminate()
                        self.host.wait(timeout=2)
                        failed = "防激活宿主未能正常卸载，已终止自己的宿主"
                if self.host.returncode != 0 and failed is None:
                    failed = (
                        "防激活宿主退出异常（exit=%s）" % self.host.returncode
                    )
        finally:
            self.host = None
            if self.log is not None:
                self.log.close()
                self.log = None
            if self.temp is not None:
                self.temp.cleanup()
                self.temp = None
                self.folder = None
        if failed:
            raise BackgroundError(failed)
