"""Nonactivating AutoHotkey bridge, restricted to captcha controls."""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


class BackgroundError(RuntimeError):
    """A background read could not be confirmed safely."""


class BackgroundInterference(BackgroundError):
    """The user desktop or foreground was unexpectedly taken by Voyager."""


def resolve_ahk_path(configured=None):
    """Use a configured/runtime executable; never download or install here."""
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    elif os.environ.get("FRYTRADER_AHK"):
        candidates.append(Path(os.environ["FRYTRADER_AHK"]).expanduser())
    else:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(
                Path(local) / "Frytrader" / "runtime" / "AutoHotkey64.exe"
            )
        discovered = shutil.which("AutoHotkey64.exe")
        if discovered:
            candidates.append(Path(discovered))
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidates.append(
                Path(program_files) / "AutoHotkey" / "v2" / "AutoHotkey64.exe"
            )
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() == ".exe":
            return candidate.resolve()
    raise BackgroundError(
        "未找到 AutoHotkey v2。请配置 ahk_path 或 FRYTRADER_AHK，"
        "后台模式不会自动安装软件或退回真实键鼠输入。"
    )


class AutoHotkeyControls:
    """Only ControlSendText and ControlClick NA; no foreground fallback."""

    def __init__(self, ahk_path=None):
        self.executable = resolve_ahk_path(ahk_path)
        self.script = (
            Path(__file__).resolve().parent
            / "config"
            / "background_controls.ahk"
        )
        if not self.script.is_file():
            raise BackgroundError("后台控件脚本未随安装包提供")

    @staticmethod
    def validate_code(code):
        if (
            not isinstance(code, str)
            or len(code) != 4
            or not code.isascii()
            or not code.isalnum()
        ):
            raise BackgroundError("验证码必须是四位 ASCII 字母或数字")

    def _run(self, action, controls, control, pid, guard, code=None):
        guard.check()
        with tempfile.TemporaryDirectory(prefix="frytrader-na-") as folder:
            directory = Path(folder)
            output = directory / "result.txt"
            command = [
                str(self.executable),
                "/ErrorStdOut",
                str(self.script),
                action,
                str(controls.dialog),
                str(control),
                str(pid),
                str(output),
            ]
            if code is not None:
                command.append(code)
            # Regular files/DEVNULL, not named-pipe stdio. No shell quoting.
            with (directory / "helper.log").open("wb") as log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                )
                try:
                    deadline = min(time.monotonic() + 5, guard.deadline)
                    while process.poll() is None:
                        guard.check()
                        if time.monotonic() >= deadline:
                            raise BackgroundError("NA 控件操作超时")
                        time.sleep(0.01)
                    guard.check()
                    result = (
                        output.read_text(encoding="utf-8-sig")
                        if output.exists()
                        else "missing result"
                    )
                    if process.returncode or result != "ok":
                        raise BackgroundError(
                            "NA 控件操作未确认（%s，exit=%s）"
                            % (action, process.returncode)
                        )
                finally:
                    # This is our short-lived bridge, never the trader.
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)

    def fill(self, controls, code, pid, guard):
        self.validate_code(code)
        self._run("fill", controls, controls.editor, pid, guard, code)

    def click(self, controls, role, pid, guard):
        if role not in ("ok", "cancel", "image"):
            raise BackgroundError("后台桥接只允许验证码对话框操作")
        self._run("click", controls, getattr(controls, role), pid, guard)
