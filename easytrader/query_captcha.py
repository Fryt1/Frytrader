"""Foreground input restricted to an identified READ-ONLY query captcha.

No orders, login, global shortcuts, or unknown-dialog confirmation are exposed.
The OCR runs separately; input deliberately waits between clearing and typing.
"""
import time
from easytrader.background_runtime import BackgroundError


class ForegroundCaptchaControls:
    def __init__(self, backend):
        self.backend = backend

    def _validate(self, controls, pid, guard):
        guard.check()
        if pid != self.backend.trader_pid or not self.backend.check_captcha(controls):
            raise BackgroundError("查询验证码的窗口身份已改变")

    def fill(self, controls, code, pid, guard):
        from easytrader.background_runtime import AutoHotkeyControls
        AutoHotkeyControls.validate_code(code)
        self._validate(controls, pid, guard)
        import win32api
        import win32gui
        import win32process
        from pywinauto import keyboard

        native = self.backend._native()
        self.backend.begin_captcha_input(controls)
        tid = native.thread_id(controls.dialog)
        caller = win32api.GetCurrentThreadId()
        attached = False
        try:
            if tid != caller:
                win32process.AttachThreadInput(caller, tid, True)
                attached = True
            # Never inject keys unless both foreground and focus are confirmed.
            try:
                win32gui.SetForegroundWindow(controls.dialog)
            except Exception:
                pass
            win32gui.SetFocus(controls.editor)
            if win32gui.GetForegroundWindow() != controls.dialog or win32gui.GetFocus() != controls.editor:
                raise BackgroundError("无法确认验证码前台与焦点，未发送任何按键")
            self._validate(controls, pid, guard)
            keyboard.send_keys("{END}{BACKSPACE 16}", pause=0.01)
            time.sleep(0.1)
            for char in code:
                self._validate(controls, pid, guard)
                if win32gui.GetForegroundWindow() != controls.dialog or win32gui.GetFocus() != controls.editor:
                    raise BackgroundError("验证码输入期间焦点改变，已停止输入")
                keyboard.send_keys(char, pause=0.035)
            time.sleep(0.1)
            self._validate(controls, pid, guard)
        finally:
            if attached:
                win32process.AttachThreadInput(caller, tid, False)

    def click(self, controls, role, pid, guard):
        self._validate(controls, pid, guard)
        native = self.backend._native()
        if role in ("ok", "cancel"):
            target = getattr(controls, role)
            if not native.post_message(target, 0x00F5):
                raise BackgroundError("查询验证码按钮消息发送失败")
        elif role == "image":
            # Static image's STN_CLICKED notification, no mouse/foreground input.
            image_id = native.control_id(controls.image)
            if not native.post_message(controls.dialog, 0x0111, image_id, controls.image):
                raise BackgroundError("验证码换图消息发送失败")
        else:
            raise BackgroundError("只允许操作查询验证码的确定、取消和换图")
