"""Offline tests for the readonly, nonactivating Voyager adapter."""

import threading
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from PIL import Image

import easytrader
from easytrader.background_runtime import (
    AutoHotkeyControls,
    BackgroundError,
    BackgroundInterference,
)
from easytrader.background_trader import (
    BackgroundOptions,
    BackgroundThsTrader,
    QueryGuard,
    _Engine,
    parse_grid,
)

HEADER = "委托时间\t证券代码\t委托数量\t合同编号\t\r\n"
REAL_ROW = "09:31:00\t000001\t100\t00001234\t\r\n"
PLACEHOLDER = "\t\t0\t\t\r\n"


class FakeBackend:
    owner_pid = 10
    trader_pid = 11
    target_desktop_id = "background"

    def __init__(self):
        self.desktop = "work"
        self.foreground = 99
        self.valid = True
        self.sequence = 100
        self.owner = self.trader_pid
        self.raw = HEADER + REAL_ROW + PLACEHOLDER
        self.copy_requested = False
        self.accepted = False
        self.image_version = 0
        self.caps = SimpleNamespace(
            dialog=1, image=2, editor=3, ok=4, cancel=5
        )
        self.restore_calls = 0
        self.protection_started = False
        self.protection_ended = False
        self.window_exposed = False
        self.pages = []

    def assert_background(self):
        if self.window_exposed:
            raise RuntimeError("visible uncloaked window")

    def ensure_render_ready(self):
        if not self.protection_started:
            raise RuntimeError("render requested without style protection")

    def validate_activation_guard(self):
        pass

    def begin_read(self):
        self.protection_started = True

    def protect_captcha(self, controls):
        if controls is not self.caps:
            raise RuntimeError("wrong captcha")

    def end_read(self):
        self.protection_ended = True

    def discover(self, path=None):
        pass

    def enter_background(self, name):
        pass

    def restore(self):
        self.restore_calls += 1

    def current_desktop_id(self):
        return self.desktop

    def foreground_pid(self):
        return self.foreground

    def identity_ok(self):
        return self.valid

    def select_page(self, path):
        self.pages.append(path)

    def balance(self):
        return {"资金余额": 42.0}

    def grid(self):
        return 7

    def copy_grid(self, grid):
        self.copy_requested = True

    def clipboard_sequence(self):
        return self.sequence

    def clipboard_owner_pid(self):
        return self.owner

    def clipboard_text(self):
        return self.raw

    def captcha(self):
        return self.caps if self.copy_requested and not self.accepted else None

    def check_captcha(self, cap):
        return cap is self.caps

    def cancel_captcha(self, cap):
        if cap is not self.caps:
            raise RuntimeError("wrong captcha")
        self.copy_requested = False

    def capture_captcha(self, image):
        result = Image.new("RGB", (16, 8), "white")
        result.putpixel((self.image_version, 1), (0, 120, 255))
        return result


class FakeRuntime:
    def __init__(self, backend, reject=0):
        self.backend = backend
        self.reject = reject
        self.calls = []

    def fill(self, cap, code, pid, guard):
        guard.check()
        self.calls.append(("fill", code))

    def click(self, cap, role, pid, guard):
        guard.check()
        self.calls.append(("click", role))
        if role == "image":
            self.backend.image_version += 1
        if role == "ok":
            if self.reject:
                self.reject -= 1
            else:
                self.backend.accepted = True
                self.backend.sequence += 1


class QuietGuard:
    def __init__(self, backend, deadline, cancelled):
        self.backend = backend
        self.deadline = deadline
        self.closed = False

    def start(self):
        self.check()

    def check(self):
        if self.backend.foreground in (10, 11):
            raise BackgroundInterference("foreground changed")

    def close(self):
        self.closed = True


class ReadonlyInterfaceTests(unittest.TestCase):
    def test_factory_selects_background_adapter_explicitly(self):
        user = easytrader.use("ths", background=True)
        self.assertIsInstance(user, BackgroundThsTrader)
        self.assertIsNone(user._thread)

    def test_unsupported_adapter_never_silently_uses_foreground_mode(self):
        with self.assertRaises(NotImplementedError):
            easytrader.use("universal_client", background=True)
        with self.assertRaises(TypeError):
            easytrader.use("ths", background="false")

    def test_trading_and_login_are_rejected_before_worker_starts(self):
        user = BackgroundThsTrader()
        for name in (
            "buy", "sell", "market_buy", "market_sell", "trade", "market_trade",
            "cancel_entrust", "cancel_all_entrusts", "auto_ipo", "repo",
            "reverse_repo", "prepare", "login", "exit",
        ):
            with self.subTest(operation=name):
                with self.assertRaises(PermissionError):
                    getattr(user, name)("irrelevant")
        self.assertIsNone(user._thread)

    def test_invalid_options_are_rejected(self):
        for options in (
            {"timeout": 0},
            {"timeout": float("nan")},
            {"captcha_retries": True},
            {"captcha_retries": 0},
            {"poll_interval": 0},
            {"allow_foreground_captcha": "yes"},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    BackgroundOptions(**options)

    def test_invalid_captcha_cannot_be_interpreted_as_keys(self):
        for text in ("^a", "{Enter}", "a%bc", "中文AB", "abcde", None):
            with self.subTest(text=text):
                with self.assertRaises(BackgroundError):
                    AutoHotkeyControls.validate_code(text)


class GridResultTests(unittest.TestCase):
    def test_leading_zeros_are_kept_and_placeholder_is_removed(self):
        rows = parse_grid(HEADER + REAL_ROW + PLACEHOLDER, "today_entrusts")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["证券代码"], "000001")
        self.assertEqual(rows[0]["合同编号"], "00001234")
        self.assertNotIn("Unnamed: 4", rows[0])

    def test_other_page_and_none_are_never_empty_success(self):
        for raw in (None, "", "成交编号\t成交金额\n"):
            with self.assertRaises(BackgroundError):
                parse_grid(raw, "today_entrusts")

    def test_real_empty_table_is_valid(self):
        self.assertEqual(
            parse_grid(HEADER + PLACEHOLDER, "today_entrusts"), []
        )

    def test_same_clipboard_sequence_is_not_new_data(self):
        backend = FakeBackend()
        backend.clipboard_text = mock.Mock(return_value=backend.raw)
        engine = _Engine(BackgroundOptions(), backend=backend)
        self.assertIsNone(engine._read_result(100, "today_entrusts"))
        backend.clipboard_text.assert_not_called()

    def test_another_clipboard_owner_aborts_instead_of_reading_user_text(self):
        backend = FakeBackend()
        backend.sequence += 1
        backend.owner = 42
        engine = _Engine(BackgroundOptions(), backend=backend)
        with self.assertRaisesRegex(BackgroundError, "其他程序"):
            engine._read_result(100, "today_entrusts")


class InterferenceTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.cancelled = threading.Event()
        self.guard = QueryGuard(
            self.backend, time.monotonic() + 10, self.cancelled
        )

    def test_user_can_change_foreground_between_other_apps(self):
        self.backend.foreground = 321
        self.guard.check()

    def test_trader_foreground_or_desktop_change_aborts(self):
        self.backend.foreground = self.backend.trader_pid
        with self.assertRaises(BackgroundInterference):
            self.guard.check()
        self.backend.foreground = 99
        self.backend.desktop = "another desktop"
        with self.assertRaises(BackgroundInterference):
            self.guard.check()

    def test_transient_desktop_switch_stays_failed_after_switching_back(self):
        self.backend.desktop = "trader desktop"
        with self.assertRaises(BackgroundInterference) as first:
            self.guard.sample()
        self.backend.desktop = "work"
        for _ in range(3):
            with self.assertRaises(BackgroundInterference) as later:
                self.guard.check()
            self.assertIs(later.exception, first.exception)

    def test_exposed_window_is_failure_even_when_foreground_did_not_change(
        self,
    ):
        self.backend.window_exposed = True
        with self.assertRaisesRegex(BackgroundInterference, "uncloaked"):
            self.guard.check()
        self.assertEqual(self.backend.foreground, 99)
        self.backend.window_exposed = False
        with self.assertRaises(BackgroundInterference):
            self.guard.check()

    def test_next_step_is_not_executed_after_observed_interference(self):
        actions = []
        self.guard.check()
        actions.append("first field")
        self.backend.foreground = self.backend.trader_pid
        with self.assertRaises(BackgroundInterference):
            self.guard.sample()
        self.backend.foreground = 99
        with self.assertRaises(BackgroundInterference):
            self.guard.check()
            actions.append("second field")
        self.assertEqual(actions, ["first field"])

    def test_stale_window_identity_aborts(self):
        self.backend.valid = False
        with self.assertRaisesRegex(BackgroundError, "窗口"):
            self.guard.check()

    def test_cancelled_request_cannot_continue_to_input(self):
        self.cancelled.set()
        with self.assertRaisesRegex(BackgroundError, "取消"):
            self.guard.check()


class EngineTests(unittest.TestCase):
    def run_engine(self, backend, runtime, **options):
        focus = mock.Mock()
        engine = _Engine(BackgroundOptions(**options), backend, runtime, focus)
        engine.connected = True
        with mock.patch(
            "easytrader.background_trader.query_lease", lambda _: nullcontext()
        ), mock.patch(
            "easytrader.background_trader.QueryGuard", QuietGuard
        ), mock.patch(
            "easytrader.background_trader.captcha_recognize_improved",
            return_value="Abc1",
        ):
            result = engine.query(
                "today_entrusts", time.monotonic() + 10, threading.Event()
            )
        return engine, result

    def test_complete_captcha_requires_real_data_not_just_closed_popup(self):
        backend = FakeBackend()
        runtime = FakeRuntime(backend)
        engine, rows = self.run_engine(backend, runtime)
        self.assertEqual(len(rows), 1)
        self.assertEqual(engine.last_report["captcha_attempts"], 1)
        self.assertEqual(runtime.calls, [("fill", "Abc1"), ("click", "ok")])

    def test_interference_during_fill_prevents_click_and_retry(self):
        backend = FakeBackend()
        runtime = FakeRuntime(backend)
        focus = mock.Mock()
        engine = _Engine(
            BackgroundOptions(page_settle=0), backend, runtime, focus
        )
        engine.connected = True

        def steal_foreground(*_):
            backend.foreground = backend.trader_pid

        runtime.fill = mock.Mock(side_effect=steal_foreground)
        runtime.click = mock.Mock()
        with mock.patch(
            "easytrader.background_trader.query_lease", lambda _: nullcontext()
        ), mock.patch.object(
            QueryGuard, "start", lambda guard: guard.check()
        ), mock.patch(
            "easytrader.background_trader.captcha_recognize_improved",
            return_value="Abc1",
        ):
            with self.assertRaises(BackgroundInterference):
                engine.query(
                    "today_entrusts", time.monotonic() + 5, threading.Event()
                )
        runtime.fill.assert_called_once()
        runtime.click.assert_not_called()
        self.assertTrue(backend.protection_ended)
        self.assertGreaterEqual(focus.close.call_count, 1)
        self.assertFalse(engine.last_report["ok"])
        self.assertEqual(backend.restore_calls, 0)

    def test_foreground_captcha_can_be_disabled_before_input(self):
        backend = FakeBackend()
        runtime = FakeRuntime(backend)
        with self.assertRaisesRegex(BackgroundError, "未允许"):
            self.run_engine(backend, runtime, allow_foreground_captcha=False)
        self.assertEqual(runtime.calls, [])
        self.assertFalse(backend.copy_requested)
        self.assertTrue(backend.protection_ended)

    def test_rejection_refreshes_before_next_attempt(self):
        backend = FakeBackend()
        runtime = FakeRuntime(backend, reject=1)
        clock = [100.0]

        def now():
            clock[0] += 0.1
            return clock[0]

        with mock.patch(
            "easytrader.background_trader.time.monotonic", side_effect=now
        ), mock.patch("easytrader.background_trader.time.sleep"):
            engine, rows = self.run_engine(backend, runtime)
        self.assertEqual(len(rows), 1)
        self.assertEqual(engine.last_report["captcha_attempts"], 2)
        self.assertEqual(runtime.calls[2], ("click", "image"))
        self.assertEqual(backend.image_version, 1)

    def test_default_close_does_not_cancel_dialog_or_restore_windows(self):
        backend = mock.Mock()
        runtime = mock.Mock()
        engine = _Engine(BackgroundOptions(), backend, runtime)
        engine.connected = True
        engine.owned_captcha = SimpleNamespace(dialog=1)
        engine.close()
        self.assertFalse(engine.connected)
        backend.restore.assert_not_called()
        backend.cancel_captcha.assert_not_called()
        runtime.click.assert_not_called()

    def test_window_restore_requires_explicit_request(self):
        backend = mock.Mock()
        engine = _Engine(BackgroundOptions(), backend, mock.Mock())
        engine.restore_windows()
        backend.restore.assert_called_once()

    def test_dependency_error_does_not_move_windows(self):
        backend = mock.Mock()
        engine = _Engine(BackgroundOptions(), backend=backend)
        with mock.patch(
            "easytrader.background_focus.ThreadActivationGuard",
            side_effect=BackgroundError("missing runtime"),
        ):
            with self.assertRaises(BackgroundError):
                engine.connect()
        backend.discover.assert_not_called()
        backend.enter_background.assert_not_called()

    def test_unconnected_reads_fail_before_native_access(self):
        engine = _Engine(BackgroundOptions(), backend=mock.Mock())
        with self.assertRaisesRegex(BackgroundError, "connect"):
            engine.query("balance", time.monotonic() + 1, threading.Event())

    def test_no_keyboard_or_foreground_fallback_on_runtime_error(self):
        backend = FakeBackend()
        runtime = FakeRuntime(backend)
        runtime.fill = mock.Mock(side_effect=BackgroundError("NA failed"))
        with self.assertRaisesRegex(BackgroundError, "NA failed"):
            self.run_engine(backend, runtime)
        self.assertEqual(runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
