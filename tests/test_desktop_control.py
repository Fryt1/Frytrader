"""Safety checks for the independent desktop entry/return controller.

No test switches the real input desktop, launches a trader, or injects input.
"""

import ctypes
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.desktop_panel import ReturnGuard, Session
from tools.win_desktop import DesktopAPI, return_to_origin, validate_name


class FakeAPI:
    def __init__(self, current="Default"):
        self.current = current
        self.switched = []
        self.refuse_switch = False

    def input_name(self):
        return self.current

    def switch(self, desktop):
        if self.refuse_switch:
            raise OSError("Windows refused SwitchDesktop")
        self.current = desktop.name
        self.switched.append(desktop.name)


class ReturnSafetyTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.origin = SimpleNamespace(
            name="Default", handle=0x1234567812345678
        )
        self.guard = ReturnGuard(self.api, self.origin, "FrytraderPrivate")

    def test_return_uses_saved_origin_not_current_input_desktop(self):
        self.api.current = "FrytraderPrivate"
        return_to_origin(self.api, self.origin, "FrytraderPrivate")
        self.assertEqual(self.api.switched, ["Default"])

    def test_already_home_does_not_switch(self):
        self.guard.return_now()
        self.assertEqual(self.api.switched, [])

    def test_secure_desktop_is_never_switched_away(self):
        for name in (None, "Winlogon", "ScreenSaver", "UnrelatedDesktop"):
            with self.subTest(desktop=name):
                self.api.current = name
                with self.assertRaises(RuntimeError):
                    self.guard.return_now()
                self.assertEqual(self.api.switched, [])

    def test_deadline_returns_without_blocking_the_gui(self):
        self.guard.arm(180)
        self.api.current = "FrytraderPrivate"
        self.guard.step(now=self.guard.deadline - 1)
        self.assertFalse(self.api.switched)
        self.guard.step(now=self.guard.deadline + 1)
        self.assertEqual(self.api.switched, ["Default"])
        self.assertIsNone(self.guard.remaining())

    def test_crashed_return_panel_triggers_controller_recovery(self):
        self.guard.arm(180)
        self.api.current = "FrytraderPrivate"
        self.guard.step(helper_alive=False)
        self.assertEqual(self.api.current, "Default")

    def test_manual_return_cancels_later_timer_switches(self):
        self.guard.arm(180)
        deadline = self.guard.deadline
        self.api.current = "FrytraderPrivate"
        self.guard.step(now=deadline - 10)
        self.api.current = "Default"
        self.guard.step(now=deadline - 9)
        self.api.current = "AnotherDesktop"
        self.guard.step(now=deadline + 1)
        self.assertEqual(self.api.switched, [])
        self.assertIsNone(self.guard.deadline)

    def test_timeout_during_lock_retries_only_after_unlock(self):
        self.guard.arm(180)
        deadline = self.guard.deadline
        self.api.current = None
        self.guard.step(now=deadline + 1)
        self.assertFalse(self.api.switched)
        self.assertIsNotNone(self.guard.deadline)
        self.api.current = "FrytraderPrivate"
        self.guard.step(now=deadline + 2)
        self.assertEqual(self.api.current, "Default")

    def test_switch_failure_keeps_recovery_armed(self):
        self.guard.arm(180)
        self.api.current = "FrytraderPrivate"
        self.api.refuse_switch = True
        self.guard.step(now=self.guard.deadline + 1)
        self.assertIsNotNone(self.guard.deadline)
        self.assertIn("refused", self.guard.error)

    def test_out_of_range_timer_is_rejected(self):
        for seconds in (0, -1, 9, 3601):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    self.guard.arm(seconds)


class SessionSafetyTests(unittest.TestCase):
    def test_not_ready_return_ui_prevents_entry(self):
        session = Session.__new__(Session)
        session.api = FakeAPI()
        session.origin = SimpleNamespace(name="Default")
        session.helper_ready = lambda: False
        with self.assertRaisesRegex(RuntimeError, "返回按钮"):
            session.enter(180)
        self.assertFalse(session.api.switched)

    def test_session_cannot_be_entered_from_secure_desktop(self):
        session = Session.__new__(Session)
        session.api = FakeAPI(None)
        session.origin = SimpleNamespace(name="Default")
        session.helper_ready = lambda: True
        with self.assertRaisesRegex(RuntimeError, "锁屏"):
            session.enter(180)
        self.assertFalse(session.api.switched)

    def test_failed_entry_disarms_both_recovery_paths(self):
        session = Session.__new__(Session)
        session.api = FakeAPI()
        session.api.refuse_switch = True
        session.origin = SimpleNamespace(name="Default")
        session.target = SimpleNamespace(name="FrytraderPrivate")
        session.guard = ReturnGuard(
            session.api, session.origin, session.target.name
        )
        session.helper_ready = lambda: True
        session.command = mock.Mock()
        with self.assertRaises(OSError):
            session.enter(180)
        self.assertIsNone(session.guard.deadline)
        session.command.assert_any_call("disarm")

    def test_existing_voyager_login_is_never_closed_or_relaunched(self):
        session = Session.__new__(Session)
        session.api = mock.Mock()
        session.api.windows.return_value = []
        session.api.trader_processes.return_value = [(123, "happ.exe")]
        session.target = SimpleNamespace(name="FrytraderPrivate")
        with mock.patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "现有登录已保留"):
                session.start_voyager(Path("happ.exe"))
        session.api.start.assert_not_called()

    def test_only_existing_voyager_executable_can_be_launched(self):
        session = Session.__new__(Session)
        for executable in ("xiadan.exe", "unrelated.exe"):
            with self.subTest(executable=executable):
                with self.assertRaises(ValueError):
                    session.start_voyager(Path(executable))

    def test_desktop_name_cannot_inject_a_station_or_command(self):
        for name in ("", "a\\b", "a/b", "a b", "Default;exit", "x" * 65):
            with self.assertRaises(ValueError):
                validate_name(name)


@unittest.skipUnless(os.name == "nt", "Windows API signatures")
class NativeSignatureTests(unittest.TestCase):
    def test_handles_use_pointer_width_instead_of_default_c_int(self):
        api = DesktopAPI()
        self.assertIs(api.user.OpenDesktopW.restype, ctypes.wintypes.HANDLE)
        self.assertIs(api.user.CreateDesktopW.restype, ctypes.wintypes.HANDLE)
        self.assertIs(
            api.user.GetThreadDesktop.restype, ctypes.wintypes.HANDLE
        )
        self.assertIs(api.kernel.CreateProcessW.restype, ctypes.wintypes.BOOL)


if __name__ == "__main__":
    unittest.main()
