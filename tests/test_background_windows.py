"""Offline tests for the same-desktop hidden-query backend.

Every Win32/DWM call is a fake: no window is enumerated, hidden, shown,
messaged or clicked for real, no client process is started, and the backend
has no virtual-desktop API available to call at all. ``FakeNative`` records
every call and raises for any attribute it does not implement, so an
accidental pyvda/desktop or window-action call fails the test.
"""

import inspect
import unittest
from unittest import mock

from easytrader import background_windows as module
from easytrader.background_windows import (
    CaptchaControls,
    WindowsBackend,
    _menu_path,
    _parse_amount,
)
from easytrader.config.client import CommonConfig

OWNER = 1
TRADER = 2
TREE = 3
GRID = 4
FOREIGN = 40
WRONG_CLASS = 41
EXTERNAL = 77

CAPTCHA = 20
CAPTCHA_IMAGE = CAPTCHA + 1
CAPTCHA_EDITOR = CAPTCHA + 2
CAPTCHA_OK = CAPTCHA + 3
CAPTCHA_CANCEL = CAPTCHA + 4

OWNER_PID = 10
TRADER_PID = 20

BALANCE_TEXT = {
    "资金余额": "1,234.50",
    "可用金额": "1000",
    "可取金额": "900",
    "股票市值": "234.5",
    "总资产": "1234.5",
}


class FakeNative:
    """A tiny window graph; nothing here talks to Windows."""

    def __init__(self):
        self.nodes = {}
        self.calls = []
        self.unexpected = []
        self.destroyed = set()
        self.caret = 12
        self.foreground = EXTERNAL
        self.input_desktop = "Default"
        self.post_result = True
        self.missing_menu = False
        self.refuse_select = False
        self.ignore_select = False
        self.fail_hide = None
        self.refuse_restore_foreground = False
        self.clipboard_owner_hwnd = TRADER
        self.raise_process_id = set()

        self._add(
            OWNER,
            title=module.VOYAGER_TITLE,
            cls="Frame",
            pid=OWNER_PID,
            visible=True,
        )
        self._add(
            TRADER,
            title=module.TRADER_TITLE_PREFIX,
            cls="Frame",
            pid=TRADER_PID,
            owner=OWNER,
            visible=True,
        )
        self._add(
            TREE,
            cls=module.QUERY_TREE_CLASS,
            pid=TRADER_PID,
            cid=module.QUERY_TREE_ID,
            parent=TRADER,
        )
        self._add(
            GRID,
            cls=module.GRID_CLASS,
            pid=TRADER_PID,
            cid=CommonConfig.COMMON_GRID_CONTROL_ID,
            parent=TRADER,
        )
        self.balance_ids = {}
        for index, (name, control_id) in enumerate(
            CommonConfig.BALANCE_CONTROL_ID_GROUP.items()
        ):
            hwnd = 100 + index
            self._add(
                hwnd,
                cls=module.BALANCE_CLASS,
                pid=TRADER_PID,
                cid=control_id,
                parent=TRADER,
                text=BALANCE_TEXT.get(name, "0"),
            )
            self.balance_ids[name] = hwnd
        self._add(FOREIGN, cls=module.GRID_CLASS, pid=99, visible=False)
        self._add(WRONG_CLASS, cls="Button", pid=TRADER_PID, visible=False)
        # A real, unrelated foreground window owned by another process.
        self._add(EXTERNAL, title="工作窗口", cls="Frame", pid=77, visible=True)
        self.foreground = EXTERNAL

    # -- graph bookkeeping -------------------------------------------
    def _add(
        self,
        hwnd,
        title="",
        cls="",
        pid=0,
        cid=0,
        parent=0,
        owner=0,
        visible=True,
        text=None,
    ):
        self.nodes[hwnd] = dict(
            title=title,
            cls=cls,
            pid=pid,
            cid=cid,
            parent=parent,
            owner=owner,
            visible=visible,
            text=text,
        )

    def add_window(self, hwnd, **kwargs):
        self._add(hwnd, **kwargs)

    def remove_window(self, hwnd):
        self.nodes.pop(int(hwnd), None)
        self.destroyed.add(int(hwnd))

    def add_captcha(self, dialog=CAPTCHA, pid=TRADER_PID):
        self._add(dialog, cls=module.CAPTCHA_DIALOG_CLASS, pid=pid)
        self._add(
            dialog + 1,
            cls="Static",
            pid=pid,
            cid=module.CAPTCHA_IMAGE_ID,
            parent=dialog,
        )
        self._add(
            dialog + 2,
            cls="Edit",
            pid=pid,
            cid=module.CAPTCHA_EDITOR_ID,
            parent=dialog,
        )
        self._add(dialog + 3, cls="Button", pid=pid, cid=1, parent=dialog)
        self._add(dialog + 4, cls="Button", pid=pid, cid=2, parent=dialog)
        return dialog

    def record(self, *call):
        self.calls.append(call)

    def names(self, action):
        return [call for call in self.calls if call[0] == action]

    # -- attributes the backend actually uses ------------------------
    def enumerate_top(self):
        return [
            hwnd
            for hwnd, node in self.nodes.items()
            if node["parent"] == 0
        ]

    def children(self, hwnd):
        return [
            child
            for child, node in self.nodes.items()
            if node["parent"] == int(hwnd)
        ]

    def text(self, hwnd):
        node = self.nodes[int(hwnd)]
        return node["text"] if node["text"] is not None else node["title"]

    def class_name(self, hwnd):
        return self.nodes[int(hwnd)]["cls"]

    def control_id(self, hwnd):
        return self.nodes[int(hwnd)]["cid"]

    def is_window(self, hwnd):
        return int(hwnd) in self.nodes and int(hwnd) not in self.destroyed

    def is_visible(self, hwnd):
        hwnd = int(hwnd)
        while hwnd:
            node = self.nodes.get(hwnd)
            if node is None or not node["visible"]:
                return False
            hwnd = node["parent"]
        return True

    def style(self, hwnd):
        if self.nodes[int(hwnd)]["visible"]:
            return module.WS_VISIBLE
        return 0

    def ex_style(self, hwnd):
        return 0

    def set_ex_style(self, hwnd, value):
        self.record("ex_style", int(hwnd), int(value))

    def owner_window(self, hwnd):
        return self.nodes[int(hwnd)]["owner"]

    def parent_window(self, hwnd):
        return self.nodes[int(hwnd)]["parent"]

    def dlg_item(self, hwnd, control_id):
        for child in self.children(hwnd):
            if self.control_id(child) == int(control_id):
                return child
        return 0

    def foreground_window(self):
        return self.foreground

    def process_id(self, hwnd):
        hwnd = int(hwnd)
        if hwnd in self.raise_process_id:
            raise OSError("process id unavailable")
        return self.nodes[hwnd]["pid"]

    def thread_id(self, hwnd):
        return int(self.nodes[int(hwnd)]["pid"]) * 3

    def process_image(self, pid):
        return {
            OWNER_PID: r"C:\ths\happ.exe",
            TRADER_PID: r"C:\ths\xiadan.exe",
        }.get(int(pid))

    def input_desktop_name(self):
        self.record("input_desktop")
        if self.input_desktop is None:
            raise RuntimeError("锁屏")
        return self.input_desktop

    def hide(self, hwnd):
        hwnd = int(hwnd)
        self.record("hide", hwnd)
        if self.fail_hide == hwnd:
            raise RuntimeError("窗口未能隐藏: %s" % hwnd)
        self.nodes[hwnd]["visible"] = False

    def set_visible_no_activate(self, hwnd):
        hwnd = int(hwnd)
        self.record("show", hwnd)
        self.nodes[hwnd]["visible"] = True
        return True

    def restore_foreground(self, hwnd):
        """Only the fake moves the fake foreground; no window is activated."""
        hwnd = int(hwnd)
        self.record("restore_foreground", hwnd)
        if self.refuse_restore_foreground:
            return False
        if not self.is_window(hwnd):
            return False
        self.foreground = hwnd
        return True

    def send_timeout(self, hwnd, message, wparam=0, lparam=0):
        self.record("send", int(hwnd), int(message), int(wparam), int(lparam))
        if message == module.TVM_GETNEXTITEM:
            return self.caret
        if message == module.TVM_SELECTITEM:
            if self.refuse_select:
                return 0
            if not self.ignore_select:
                self.caret = int(lparam)
            return 1
        raise AssertionError("unexpected message 0x%04X" % int(message))

    def post_message(self, hwnd, message, wparam=0, lparam=0):
        self.record("post", int(hwnd), int(message), int(wparam), int(lparam))
        return self.post_result

    def clipboard_sequence(self):
        return 100

    def clipboard_text(self):
        return "header\r\n"

    def clipboard_owner(self):
        return self.clipboard_owner_hwnd

    def tree_item(self, hwnd, path):
        self.record("tree_item", int(hwnd), tuple(path))
        if self.missing_menu:
            raise RuntimeError("菜单项不存在")
        return 55

    # -- guard --------------------------------------------------------
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            unexpected = object.__getattribute__(self, "unexpected")
        except AttributeError:
            raise AttributeError(name) from None
        unexpected.append(name)
        raise AttributeError("unexpected native API: %s" % name)


class BackendTestCase(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.backend = WindowsBackend(self.native)

    def tearDown(self):
        self.assertEqual(self.native.unexpected, [])

    def reset(self):
        """A completely fresh fake graph; nothing leaks between sub-cases."""
        native = FakeNative()
        self.native = native
        self.backend = WindowsBackend(native)
        return native

    def discover(self, exe_path=r"C:\ths\xiadan.exe"):
        self.backend.discover(exe_path)

    def enter(self):
        self.discover()
        self.backend.enter_background()

    def popped_captcha(self, dialog=CAPTCHA):
        self.native.add_captcha(dialog)
        return CaptchaControls(
            dialog, dialog + 1, dialog + 2, dialog + 3, dialog + 4
        )


class DiscoveryTests(BackendTestCase):
    def test_discovers_the_unique_owner_trader_and_query_tree(self):
        self.discover()
        self.assertEqual(self.backend.owner_hwnd, OWNER)
        self.assertEqual(self.backend.trader_hwnd, TRADER)
        self.assertEqual(self.backend.owner_pid, OWNER_PID)
        self.assertEqual(self.backend.trader_pid, TRADER_PID)
        self.assertEqual(self.backend._tree_hwnd, TREE)
        self.assertEqual(self.backend._original_caret, 12)
        self.assertTrue(self.backend.identity_ok())

    def test_duplicate_or_missing_owner_window_is_rejected(self):
        self.native.add_window(
            50, title=module.VOYAGER_TITLE, cls="Frame", pid=10
        )
        with self.assertRaisesRegex(RuntimeError, "需要且仅需要一个"):
            self.discover()
        self.reset().remove_window(OWNER)
        with self.assertRaisesRegex(RuntimeError, "需要且仅需要一个"):
            self.discover()

    def test_duplicate_or_missing_trader_window_is_rejected(self):
        self.native.add_window(
            51,
            title=module.TRADER_TITLE_PREFIX + " - 买入",
            cls="Frame",
            pid=20,
            owner=OWNER,
        )
        with self.assertRaisesRegex(RuntimeError, "需要且仅需要一个"):
            self.discover()
        self.reset().remove_window(TRADER)
        with self.assertRaisesRegex(RuntimeError, "需要且仅需要一个"):
            self.discover()

    def test_trader_must_belong_to_the_discovered_owner(self):
        self.native.nodes[TRADER]["owner"] = 0
        with self.assertRaisesRegex(RuntimeError, "不属于该远航版窗口"):
            self.discover()

    def test_non_voyager_executables_are_rejected(self):
        self.native.process_image = lambda pid: r"C:\evil\other.exe"
        with self.assertRaisesRegex(RuntimeError, "happ.exe"):
            self.discover()
        self.native.process_image = lambda pid: {
            OWNER_PID: r"C:\ths\happ.exe",
            TRADER_PID: r"C:\ths\other.exe",
        }[int(pid)]
        with self.assertRaisesRegex(RuntimeError, "xiadan.exe"):
            self.discover()

    def test_explicit_client_path_mismatch_touches_no_window(self):
        with self.assertRaisesRegex(RuntimeError, "路径"):
            self.discover(r"C:\other\xiadan.exe")
        self.assertEqual(self.native.names("hide"), [])
        self.assertEqual(self.native.names("show"), [])
        self.assertFalse(self.backend._hidden_active)

    def test_missing_or_ambiguous_query_tree_is_rejected(self):
        self.native.remove_window(TREE)
        with self.assertRaisesRegex(RuntimeError, "查询菜单尚未就绪"):
            self.discover()
        self.native._add(
            TREE,
            cls=module.QUERY_TREE_CLASS,
            pid=TRADER_PID,
            cid=module.QUERY_TREE_ID,
            parent=TRADER,
        )
        self.native.destroyed.clear()
        self.discover()
        self.native.add_window(
            52,
            cls=module.QUERY_TREE_CLASS,
            pid=TRADER_PID,
            cid=module.QUERY_TREE_ID,
            parent=TRADER,
        )
        with self.assertRaisesRegex(RuntimeError, "查询菜单尚未就绪"):
            self.discover()

    def test_recycled_window_pid_is_reported_as_stale(self):
        self.discover()
        self.native.nodes[TRADER]["pid"] = 21
        self.assertFalse(self.backend.identity_ok())

    def test_discover_twice_while_hidden_is_refused(self):
        self.enter()
        with self.assertRaisesRegex(RuntimeError, "隐藏查询会话仍在使用"):
            self.discover()

    def test_constructor_defers_native_and_desktop_helpers(self):
        with mock.patch.object(
            module, "_Native", side_effect=AssertionError("native created")
        ):
            backend = WindowsBackend()
            backend.restore()
        for name in ("_Desktops", "DEFAULT_DESKTOP_NAME", "_desktop_name"):
            self.assertFalse(hasattr(module, name), name)
        for name in ("_desktops", "target_desktop_id", "source_desktop_id"):
            self.assertFalse(hasattr(self.backend, name), name)
        self.assertEqual(
            list(
                inspect.signature(
                    WindowsBackend.enter_background
                ).parameters
            ),
            ["self"],
        )
        with self.assertRaises(TypeError):
            self.backend.enter_background("Frytrader 后台")


class HiddenSessionTests(BackendTestCase):
    def test_enter_background_hides_trader_then_owner(self):
        self.enter()
        hides = [call for call in self.native.names("hide")]
        self.assertEqual(hides, [("hide", TRADER), ("hide", OWNER)])
        self.assertTrue(self.backend._hidden_active)
        self.assertFalse(self.native.is_visible(TRADER))
        self.assertFalse(self.native.is_visible(OWNER))

    def test_enter_background_requires_a_discovered_client(self):
        with self.assertRaisesRegex(RuntimeError, "discover"):
            self.backend.enter_background()
        self.assertEqual(self.native.names("hide"), [])

    def test_enter_background_is_not_repeatable(self):
        self.enter()
        with self.assertRaisesRegex(RuntimeError, "已经开始"):
            self.backend.enter_background()

    def test_hide_failure_is_never_reported_as_a_usable_session(self):
        self.discover()
        self.native.fail_hide = OWNER
        with self.assertRaisesRegex(RuntimeError, "隐藏"):
            self.backend.enter_background()
        with self.assertRaisesRegex(RuntimeError, "失效"):
            self.backend.assert_background()

    def test_assert_background_requires_hidden_session(self):
        self.discover()
        with self.assertRaisesRegex(RuntimeError, "失效"):
            self.backend.assert_background()

    def test_visible_owner_or_trader_window_fails_the_check(self):
        self.enter()
        for hwnd in (OWNER, TRADER):
            with self.subTest(hwnd=hwnd):
                self.native.nodes[hwnd]["visible"] = True
                with self.assertRaisesRegex(RuntimeError, "非验证码"):
                    self.backend.assert_background()
                self.native.nodes[hwnd]["visible"] = False
        self.backend.assert_background()

    def test_unidentified_client_popup_is_not_a_permitted_window(self):
        self.enter()
        self.native.add_window(
            60,
            title="提示",
            cls=module.CAPTCHA_DIALOG_CLASS,
            pid=TRADER_PID,
        )
        with self.assertRaisesRegex(RuntimeError, "非验证码"):
            self.backend.assert_background()

    def test_windows_of_other_processes_are_ignored(self):
        self.enter()
        self.native.add_window(61, title="其他程序", cls="Frame", pid=77)
        self.backend.assert_background()

    def test_captcha_visibility_requires_explicit_permission(self):
        self.enter()
        self.popped_captcha()
        with self.assertRaisesRegex(RuntimeError, "非验证码"):
            self.backend.assert_background()
        self.backend.allow_query_captcha(True)
        self.backend.assert_background()
        self.backend.allow_query_captcha(False)
        with self.assertRaisesRegex(RuntimeError, "非验证码"):
            self.backend.assert_background()

    def test_more_than_one_captcha_is_never_permitted(self):
        self.enter()
        self.popped_captcha(CAPTCHA)
        self.popped_captcha(70)
        self.backend.allow_query_captcha(True)
        with self.assertRaisesRegex(RuntimeError, "多个验证码"):
            self.backend.assert_background()

    def test_captcha_with_changed_identity_is_not_permitted(self):
        self.enter()
        self.popped_captcha()
        self.backend.allow_query_captcha(True)
        self.native.nodes[CAPTCHA_EDITOR]["pid"] = 99
        with self.assertRaisesRegex(RuntimeError, "非验证码"):
            self.backend.assert_background()

    def test_uncheckable_window_visibility_aborts(self):
        self.enter()
        self.native.add_window(62, cls="Frame", pid=TRADER_PID)
        self.native.raise_process_id.add(62)
        with self.assertRaisesRegex(RuntimeError, "无法确认"):
            self.backend.assert_background()

    def test_ensure_render_ready_never_shows_a_window(self):
        self.enter()
        self.backend.ensure_render_ready()
        self.assertEqual(self.native.names("show"), [])
        self.assertFalse(self.native.is_visible(TRADER))
        self.assertFalse(self.native.is_visible(OWNER))

    def test_ensure_render_ready_requires_the_hidden_session(self):
        self.discover()
        with self.assertRaisesRegex(RuntimeError, "失效"):
            self.backend.ensure_render_ready()

    def test_begin_read_only_re_asserts_the_background(self):
        self.enter()
        self.native.calls[:] = []
        self.backend.begin_read()
        self.assertEqual(self.native.names("show"), [])
        self.assertEqual(self.native.names("hide"), [])
        self.assertEqual(self.native.names("send"), [])


class ForegroundPermissionTests(BackendTestCase):
    def test_permission_requires_flag_and_valid_captcha(self):
        self.enter()
        self.assertFalse(self.backend.foreground_permitted())
        cap = self.popped_captcha()
        self.assertFalse(self.backend.foreground_permitted())
        self.backend.allow_query_captcha(True)
        self.native.foreground = cap.dialog
        self.assertTrue(self.backend.foreground_permitted())

    def test_each_validated_captcha_handle_may_own_the_foreground(self):
        self.enter()
        cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)
        for handle in (
            cap.dialog,
            cap.editor,
            cap.ok,
            cap.cancel,
            cap.image,
        ):
            with self.subTest(handle=handle):
                self.native.foreground = handle
                self.assertTrue(self.backend.foreground_permitted())

    def test_any_other_foreground_window_is_not_permitted(self):
        self.enter()
        self.popped_captcha()
        self.backend.allow_query_captcha(True)
        for handle in (OWNER, TRADER, 0, 999):
            with self.subTest(handle=handle):
                self.native.foreground = handle
                self.assertFalse(self.backend.foreground_permitted())

    def test_captcha_with_changed_controls_is_not_permitted(self):
        self.enter()
        cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)
        self.native.foreground = cap.dialog
        self.native.nodes[CAPTCHA_EDITOR]["parent"] = 0
        self.assertFalse(self.backend.foreground_permitted())

    def test_protect_captcha_accepts_only_a_permitted_identity(self):
        self.enter()
        cap = self.popped_captcha()
        with self.assertRaisesRegex(RuntimeError, "不是本次查询"):
            self.backend.protect_captcha(cap)
        self.backend.allow_query_captcha(True)
        self.backend.protect_captcha(cap)
        self.native.nodes[CAPTCHA_IMAGE]["cid"] = 0x1234
        with self.assertRaisesRegex(RuntimeError, "不是本次查询"):
            self.backend.protect_captcha(cap)

    def test_end_read_revokes_permission_and_rehides_the_mains(self):
        self.enter()
        cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)
        self.native.foreground = cap.dialog
        self.assertTrue(self.backend.foreground_permitted())
        self.native.calls[:] = []
        self.backend.end_read()
        self.assertEqual(
            self.native.names("hide"), [("hide", TRADER), ("hide", OWNER)]
        )
        self.assertEqual(self.native.names("show"), [])
        self.assertFalse(self.backend.foreground_permitted())
        with self.assertRaisesRegex(RuntimeError, "非验证码"):
            self.backend.assert_background()


class CaptchaInputTransitionTests(BackendTestCase):
    """Only the captcha and the hidden-main close transition may own focus."""

    def setUp(self):
        super().setUp()
        self.enter()
        self.cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)

    def test_begin_captcha_input_requires_permission_and_exact_identity(self):
        stale = CaptchaControls(self.cap.dialog, 5, 6, 7, 8)
        with self.assertRaisesRegex(RuntimeError, "未授权"):
            self.backend.begin_captcha_input(stale)
        self.backend.allow_query_captcha(False)
        with self.assertRaisesRegex(RuntimeError, "未授权"):
            self.backend.begin_captcha_input(self.cap)
        self.assertFalse(self.backend._captcha_input_active)
        self.backend.allow_query_captcha(True)
        self.backend.begin_captcha_input(self.cap)
        self.assertTrue(self.backend._captcha_input_active)

    def test_hidden_mains_are_permitted_only_while_input_is_active(self):
        for handle in (OWNER, TRADER):
            with self.subTest(handle=handle):
                self.native.foreground = handle
                self.assertFalse(self.backend.foreground_permitted())
                self.backend.begin_captcha_input(self.cap)
                self.assertTrue(self.backend.foreground_permitted())
                self.native.nodes[handle]["visible"] = True
                self.assertFalse(self.backend.foreground_permitted())
                self.native.nodes[handle]["visible"] = False
                self.backend._captcha_input_active = False

    def test_captcha_foreground_never_needs_the_transition_flag(self):
        self.assertFalse(self.backend._captcha_input_active)
        for handle in (
            self.cap.dialog,
            self.cap.editor,
            self.cap.ok,
            self.cap.cancel,
            self.cap.image,
        ):
            with self.subTest(handle=handle):
                self.native.foreground = handle
                self.assertTrue(self.backend.foreground_permitted())

    def test_transition_stays_closed_without_permission(self):
        self.backend.begin_captcha_input(self.cap)
        self.native.foreground = TRADER
        self.backend.allow_query_captcha(False)
        self.assertFalse(self.backend.foreground_permitted())

    def test_unrelated_foreground_is_never_permitted_in_transition(self):
        self.backend.begin_captcha_input(self.cap)
        for handle in (0, 77, 999):
            with self.subTest(handle=handle):
                self.native.foreground = handle
                self.assertFalse(self.backend.foreground_permitted())


class BeginReadSnapshotTests(BackendTestCase):
    def test_external_foreground_is_snapshotted(self):
        self.enter()
        self.assertEqual(self.native.foreground, EXTERNAL)
        self.backend.begin_read()
        self.assertEqual(self.backend._return_foreground, EXTERNAL)

    def test_client_owned_foreground_is_not_a_restore_target(self):
        self.native.foreground = TRADER
        self.enter()
        self.backend.begin_read()
        self.assertEqual(self.backend._return_foreground, 0)
        self.native.foreground = 0
        self.backend.begin_read()
        self.assertEqual(self.backend._return_foreground, 0)

    def test_begin_read_resets_the_captcha_transition_flag(self):
        self.enter()
        cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)
        self.backend.begin_captcha_input(cap)
        self.assertTrue(self.backend._captcha_input_active)
        self.backend.begin_read()
        self.assertFalse(self.backend._captcha_input_active)


class EndReadForegroundReturnTests(BackendTestCase):
    def setUp(self):
        super().setUp()
        self.enter()
        self.cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)
        self.backend.begin_read()
        self.assertEqual(self.backend._return_foreground, EXTERNAL)

    def test_foreground_is_returned_when_the_client_still_owns_it(self):
        self.backend.begin_captcha_input(self.cap)
        self.native.foreground = self.cap.editor
        self.native.calls[:] = []
        self.backend.end_read()
        self.assertEqual(
            self.native.names("restore_foreground"),
            [("restore_foreground", EXTERNAL)],
        )
        self.assertEqual(self.native.foreground, EXTERNAL)
        self.assertFalse(self.backend._captcha_input_active)

    def test_no_steal_when_the_external_window_is_already_active(self):
        self.backend.begin_captcha_input(self.cap)
        self.native.foreground = EXTERNAL
        self.native.calls[:] = []
        self.backend.end_read()
        self.assertEqual(self.native.names("restore_foreground"), [])
        self.assertEqual(self.native.foreground, EXTERNAL)

    def test_no_restore_without_captcha_input(self):
        self.native.foreground = self.cap.dialog
        self.native.calls[:] = []
        self.backend.end_read()
        self.assertEqual(self.native.names("restore_foreground"), [])
        self.assertEqual(self.native.foreground, self.cap.dialog)

    def test_no_restore_without_a_snapshot(self):
        native = self.reset()
        native.foreground = 0
        self.enter()
        cap = self.popped_captcha()
        self.backend.allow_query_captcha(True)
        self.backend.begin_read()
        self.assertEqual(self.backend._return_foreground, 0)
        self.backend.begin_captcha_input(cap)
        native.foreground = cap.editor
        with self.assertRaisesRegex(RuntimeError, "归还前台"):
            self.backend.end_read()
        self.assertEqual(native.names("restore_foreground"), [])

    def test_restore_failure_is_reported_not_hidden(self):
        self.backend.begin_captcha_input(self.cap)
        self.native.foreground = self.cap.editor
        self.native.refuse_restore_foreground = True
        self.native.calls[:] = []
        with self.assertRaisesRegex(RuntimeError, "归还前台"):
            self.backend.end_read()
        self.assertEqual(
            self.native.names("hide"), [("hide", TRADER), ("hide", OWNER)]
        )
        self.assertFalse(self.backend._query_captcha_allowed)

    def test_restore_to_a_destroyed_window_is_a_failure(self):
        self.backend.begin_captcha_input(self.cap)
        self.native.foreground = self.cap.editor
        self.native.remove_window(EXTERNAL)
        with self.assertRaisesRegex(RuntimeError, "归还前台"):
            self.backend.end_read()

    def test_mains_are_hidden_before_the_foreground_is_returned(self):
        self.backend.begin_captcha_input(self.cap)
        self.native.foreground = self.cap.editor
        self.native.calls[:] = []
        self.backend.end_read()
        order = [call[0] for call in self.native.calls]
        self.assertLess(order.index("hide"), order.index("restore_foreground"))


class RestoreTests(BackendTestCase):
    def test_restore_before_entering_does_nothing(self):
        self.discover()
        self.backend.restore()
        self.assertEqual(self.native.names("show"), [])
        self.assertEqual(self.native.names("hide"), [])
        self.assertFalse(self.backend._hidden_active)

    def test_restore_is_explicit_and_restores_original_visibility(self):
        self.enter()
        self.assertEqual(self.native.names("show"), [])
        self.backend.restore()
        self.assertEqual(
            self.native.names("show"), [("show", OWNER), ("show", TRADER)]
        )
        self.assertTrue(self.native.is_visible(OWNER))
        self.assertTrue(self.native.is_visible(TRADER))
        self.assertFalse(self.backend._hidden_active)
        self.native.calls[:] = []
        self.backend.restore()
        self.assertEqual(self.native.calls, [])

    def test_restore_keeps_originally_hidden_windows_hidden(self):
        self.native.nodes[TRADER]["visible"] = False
        self.enter()
        self.backend.restore()
        self.assertEqual(self.native.names("show"), [("show", OWNER)])
        self.assertNotIn(("show", TRADER), self.native.names("show"))
        self.assertFalse(self.native.is_visible(TRADER))

    def test_restore_is_refused_while_a_captcha_is_pending(self):
        self.enter()
        self.popped_captcha()
        with self.assertRaisesRegex(RuntimeError, "验证码"):
            self.backend.restore()
        self.assertEqual(self.native.names("show"), [])
        self.assertTrue(self.backend._hidden_active)

    def test_restore_puts_the_query_menu_back(self):
        self.enter()
        self.backend.select_page(["查询[F4]", "当日委托"])
        self.assertEqual(self.native.caret, 55)
        self.backend.restore()
        self.assertEqual(self.native.caret, 12)


class QueryControlTests(BackendTestCase):
    def test_menu_path_must_be_a_list_of_two_non_empty_strings(self):
        for path in ("查询[F4]", ["查询[F4]"], ["查询[F4]", " "], ["a", 1]):
            with self.subTest(path=path):
                with self.assertRaises(RuntimeError):
                    _menu_path(path)

    def test_select_page_moves_the_caret_only_when_needed(self):
        self.enter()
        self.backend.select_page(["查询[F4]", "当日成交"])
        self.assertEqual(self.native.caret, 55)
        self.native.calls[:] = []
        self.backend.select_page(["查询[F4]", "当日成交"])
        self.assertEqual(
            [
                call
                for call in self.native.names("send")
                if call[2] == module.TVM_SELECTITEM
            ],
            [],
        )

    def test_select_page_rejects_a_refused_or_ignored_switch(self):
        self.enter()
        self.native.refuse_select = True
        with self.assertRaisesRegex(RuntimeError, "未接受"):
            self.backend.select_page(["查询[F4]", "当日成交"])
        self.native.refuse_select = False
        self.native.ignore_select = True
        with self.assertRaisesRegex(RuntimeError, "未生效"):
            self.backend.select_page(["查询[F4]", "当日成交"])

    def test_menu_control_identity_is_revalidated(self):
        self.enter()
        self.native.destroyed.add(TREE)
        with self.assertRaisesRegex(RuntimeError, "失效"):
            self.backend.select_page(["查询[F4]", "当日成交"])
        self.native.destroyed.clear()
        self.native.nodes[TREE]["cls"] = "Button"
        with self.assertRaisesRegex(RuntimeError, "类已改变"):
            self.backend.select_page(["查询[F4]", "当日成交"])
        self.native.nodes[TREE]["cls"] = module.QUERY_TREE_CLASS
        self.native.nodes[TREE]["cid"] = 130
        with self.assertRaisesRegex(RuntimeError, "ID 已改变"):
            self.backend.select_page(["查询[F4]", "当日成交"])

    def test_missing_menu_item_does_not_send_a_selection(self):
        self.enter()
        self.native.missing_menu = True
        with self.assertRaisesRegex(RuntimeError, "菜单项不存在"):
            self.backend.select_page(["查询[F4]", "不存在"])
        self.assertEqual(
            [
                call
                for call in self.native.names("send")
                if call[2] == module.TVM_SELECTITEM
            ],
            [],
        )

    def test_balance_is_parsed_from_layout_visible_fields(self):
        self.enter()
        result = self.backend.balance()
        expected = {
            name: float(BALANCE_TEXT.get(name, "0").replace(",", ""))
            for name in CommonConfig.BALANCE_CONTROL_ID_GROUP
        }
        self.assertEqual(result, expected)

    def test_balance_rejects_empty_missing_or_non_finite_values(self):
        self.enter()
        hwnd = self.native.balance_ids["资金余额"]
        for raw, message in (
            ("", "为空"),
            ("abc", "不是数字"),
            ("nan", "不是有限数字"),
        ):
            with self.subTest(raw=raw):
                self.native.nodes[hwnd]["text"] = raw
                with self.assertRaisesRegex(RuntimeError, message):
                    self.backend.balance()

    def test_balance_rejects_an_ambiguous_field(self):
        self.enter()
        self.native.add_window(
            120,
            cls=module.BALANCE_CLASS,
            pid=TRADER_PID,
            cid=CommonConfig.BALANCE_CONTROL_ID_GROUP["资金余额"],
            parent=TRADER,
            text="1",
        )
        with self.assertRaisesRegex(RuntimeError, "可见控件数为 2"):
            self.backend.balance()

    def test_parse_amount_keeps_thousands_separators_out(self):
        self.assertEqual(_parse_amount(" 1,234.50 ", "x"), 1234.5)
        with self.assertRaises(RuntimeError):
            _parse_amount("inf", "x")

    def test_grid_requires_exactly_one_layout_visible_table(self):
        self.enter()
        self.assertEqual(self.backend.grid(), GRID)
        self.native.add_window(
            121,
            cls=module.GRID_CLASS,
            pid=TRADER_PID,
            cid=CommonConfig.COMMON_GRID_CONTROL_ID,
            parent=TRADER,
        )
        with self.assertRaisesRegex(RuntimeError, "表格数为 2"):
            self.backend.grid()

    def test_grid_copy_rejects_foreign_or_wrong_controls(self):
        self.enter()
        with self.assertRaisesRegex(RuntimeError, "不属于下单窗口"):
            self.backend.copy_grid(FOREIGN)
        with self.assertRaisesRegex(RuntimeError, "不是 CVirtualGridCtrl"):
            self.backend.copy_grid(WRONG_CLASS)
        with self.assertRaisesRegex(RuntimeError, "失效"):
            self.backend.copy_grid(999)
        self.assertEqual(self.native.names("post"), [])

    def test_grid_copy_is_a_command_not_a_mouse_or_keyboard_event(self):
        self.enter()
        self.backend.copy_grid(self.backend.grid())
        self.assertEqual(
            self.native.names("post"),
            [("post", GRID, module.WM_COMMAND, module.GRID_COPY_COMMAND, 0)],
        )

    def test_clipboard_owner_is_only_read(self):
        self.enter()
        self.assertEqual(self.backend.clipboard_owner_pid(), TRADER_PID)
        self.native.clipboard_owner_hwnd = 0
        self.assertIsNone(self.backend.clipboard_owner_pid())
        self.assertEqual(self.native.names("post"), [])

    def test_captcha_identity_check_rejects_tampered_controls(self):
        self.enter()
        cap = self.popped_captcha()
        self.assertTrue(self.backend.check_captcha(cap))
        with self.assertRaisesRegex(RuntimeError, "CaptchaControls"):
            self.backend.check_captcha(object())
        self.native.nodes[CAPTCHA_IMAGE]["parent"] = 0
        self.assertFalse(self.backend.check_captcha(cap))

    def test_cancel_captcha_only_targets_the_identified_dialog(self):
        self.enter()
        cap = self.popped_captcha()
        with self.assertRaisesRegex(RuntimeError, "已改变"):
            self.backend.cancel_captcha(
                CaptchaControls(cap.dialog, 5, 6, 7, 8)
            )
        self.assertEqual(self.native.names("post"), [])
        self.backend.cancel_captcha(cap)
        self.assertEqual(
            self.native.names("post"),
            [("post", CAPTCHA_CANCEL, 0x00F5, 0, 0)],
        )

    def test_capture_captcha_validates_before_touching_any_image(self):
        self.enter()
        self.popped_captcha()
        with self.assertRaisesRegex(RuntimeError, "窗口句柄无效"):
            self.backend.capture_captcha(0)
        with self.assertRaisesRegex(RuntimeError, "失效"):
            self.backend.capture_captcha(999)
        with self.assertRaisesRegex(RuntimeError, "不属于下单窗口"):
            self.backend.capture_captcha(FOREIGN)
        with self.assertRaisesRegex(RuntimeError, "控件 ID 已改变"):
            self.backend.capture_captcha(CAPTCHA_EDITOR)

    def test_input_desktop_is_only_read(self):
        self.enter()
        self.assertEqual(self.backend.current_desktop_id(), "Default")
        self.native.input_desktop = None
        with self.assertRaisesRegex(RuntimeError, "锁屏"):
            self.backend.current_desktop_id()

    def test_foreground_pid_maps_the_foreground_window_only(self):
        self.enter()
        self.assertEqual(self.backend.foreground_pid(), 77)
        self.native.foreground = TRADER
        self.assertEqual(self.backend.foreground_pid(), TRADER_PID)
        self.native.foreground = 0
        self.assertEqual(self.backend.foreground_pid(), 0)


if __name__ == "__main__":
    unittest.main()
