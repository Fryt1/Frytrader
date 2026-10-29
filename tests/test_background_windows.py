"""Offline lifecycle tests; every window/desktop API is a fake."""

import unittest
from types import SimpleNamespace
from unittest import mock

from easytrader import background_windows as module
from easytrader.background_windows import WindowsBackend


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {
            1: dict(
                title="同花顺远航版", cls="Frame", pid=10, cid=0, parent=0
            ),
            2: dict(
                title="网上股票交易系统5.0",
                cls="Frame",
                pid=20,
                cid=0,
                parent=0,
            ),
            3: dict(title="", cls="SysTreeView32", pid=20, cid=129, parent=2),
            4: dict(
                title="", cls="CVirtualGridCtrl", pid=20, cid=1047, parent=2
            ),
        }
        self.location = {1: "work"}
        self.ids = {"work"}
        self.caret = 12
        self.native = mock.Mock()
        self.native.enumerate_top.return_value = [1, 2]
        self.native.children.return_value = [3, 4]
        self.native.text.side_effect = lambda h: self.nodes[h]["title"]
        self.native.class_name.side_effect = lambda h: self.nodes[h]["cls"]
        self.native.process_id.side_effect = lambda h: self.nodes[h]["pid"]
        self.native.control_id.side_effect = lambda h: self.nodes[h]["cid"]
        self.native.parent_window.side_effect = lambda h: self.nodes[h][
            "parent"
        ]
        self.native.owner_window.return_value = 1
        self.native.is_window.side_effect = lambda h: h in self.nodes
        self.native.is_visible.return_value = True
        self.native.style.return_value = module.WS_VISIBLE
        self.native.cloaked.side_effect = lambda h: (
            2 if self.location[1] != "work" else 0
        )
        self.native.process_image.side_effect = lambda p: (
            r"C:\ths\happ.exe" if p == 10 else r"C:\ths\xiadan.exe"
        )
        self.native.send_timeout.side_effect = self.send
        self.native.tree_item.return_value = 55
        self.native.post_message.return_value = True
        self.native.set_visible_no_activate.return_value = True
        self.desktops = mock.Mock()
        self.desktops.current_id.return_value = "work"
        self.desktops.view_desktop_id.side_effect = lambda h: self.location[h]
        self.desktops.is_pinned.return_value = False
        self.desktops.create.side_effect = self.create
        self.desktops.move.side_effect = (
            lambda h, d: self.location.__setitem__(h, d)
        )
        self.desktops.exists.side_effect = lambda d: d in self.ids
        self.desktops.apps.side_effect = lambda d: [
            h for h, v in self.location.items() if v == d
        ]
        self.desktops.remove.side_effect = lambda d: self.ids.remove(d)
        self.backend = WindowsBackend(self.native, self.desktops)

    def send(self, hwnd, message, wparam=0, lparam=0):
        if message == module.TVM_GETNEXTITEM:
            return self.caret
        if message == module.TVM_SELECTITEM:
            self.caret = lparam
            return 1
        return 1

    def create(self, name):
        self.ids.add("owned")
        return "owned"

    def discover(self):
        self.backend.discover(r"C:\ths\xiadan.exe")

    def test_constructor_and_unused_restore_never_touch_windows(self):
        with mock.patch.object(
            module, "_Native", side_effect=AssertionError
        ), mock.patch.object(module, "_Desktops", side_effect=AssertionError):
            backend = WindowsBackend()
            backend.restore()

    def test_connected_but_not_entered_restore_is_noop(self):
        self.discover()
        self.caret = 99
        self.native.send_timeout.reset_mock()
        self.backend.restore()
        self.assertEqual(self.caret, 99)
        self.native.send_timeout.assert_not_called()
        self.desktops.move.assert_not_called()

    def test_entry_and_explicit_restore_only_manage_own_desktop(self):
        self.discover()
        self.backend.enter_background()
        self.assertEqual(self.location[1], "owned")
        self.backend.select_page(["查询[F4]", "当日委托"])
        self.assertEqual(self.caret, 55)
        self.backend.restore()
        self.assertEqual(self.location[1], "work")
        self.assertEqual(self.caret, 12)
        self.assertEqual(self.ids, {"work"})
        self.backend.restore()
        self.desktops.remove.assert_called_once_with("owned")

    def test_existing_background_desktop_is_borrowed_without_moving(self):
        self.location[1] = "existing"
        self.ids.add("existing")
        self.discover()
        self.backend.enter_background()
        self.assertEqual(self.backend.target_desktop_id, "existing")
        self.backend.restore()
        self.desktops.create.assert_not_called()
        self.desktops.move.assert_not_called()
        self.desktops.remove.assert_not_called()
        self.assertIn("existing", self.ids)

    def test_manual_move_preserves_users_page_and_placement(self):
        self.discover()
        self.backend.enter_background()
        self.location[1] = "manual"
        self.ids.add("manual")
        self.caret = 88
        self.desktops.move.reset_mock()
        self.backend.restore()
        self.desktops.move.assert_not_called()
        self.assertEqual(self.location[1], "manual")
        self.assertEqual(self.caret, 88)

    def test_pinned_window_is_not_moved(self):
        self.discover()
        self.desktops.is_pinned.return_value = True
        with self.assertRaisesRegex(RuntimeError, "固定"):
            self.backend.enter_background()
        self.desktops.create.assert_not_called()
        self.desktops.move.assert_not_called()

    def test_failure_during_entry_rolls_back_only_empty_owned_desktop(self):
        self.discover()

        def move(hwnd, desktop):
            self.location[hwnd] = desktop
            if desktop == "owned":
                self.desktops.current_id.return_value = "user_switched"

        self.desktops.move.side_effect = move
        with self.assertRaisesRegex(RuntimeError, "桌面"):
            self.backend.enter_background()
        self.assertEqual(self.location[1], "work")
        self.assertNotIn("owned", self.ids)

    def test_refuses_to_remove_current_desktop(self):
        self.discover()
        self.backend.enter_background()
        self.desktops.current_id.return_value = "owned"
        with self.assertRaisesRegex(RuntimeError, "活动"):
            self.backend.restore()
        self.desktops.remove.assert_not_called()
        self.assertIn("owned", self.ids)

    def test_background_check_accepts_cloaked_windows_without_moving_them(
        self,
    ):
        self.discover()
        self.backend.enter_background()
        self.desktops.move.reset_mock()
        self.backend.assert_background()
        self.desktops.move.assert_not_called()
        self.native.set_visible_no_activate.assert_not_called()

    def test_background_check_rejects_uncloaked_or_unknown_window(self):
        self.discover()
        self.backend.enter_background()
        self.desktops.move.reset_mock()
        for state in (0, None):
            with self.subTest(cloaked=state):
                self.native.cloaked.side_effect = None
                self.native.cloaked.return_value = state
                with self.assertRaisesRegex(RuntimeError, "未遮蔽"):
                    self.backend.assert_background()
        self.desktops.move.assert_not_called()
        self.native.set_visible_no_activate.assert_not_called()

    def test_background_check_rejects_current_target_desktop(self):
        self.discover()
        self.backend.enter_background()
        self.desktops.current_id.return_value = "owned"
        with self.assertRaisesRegex(RuntimeError, "当前桌面"):
            self.backend.assert_background()

    def test_background_check_rejects_external_window_move(self):
        self.discover()
        self.backend.enter_background()
        self.location[1] = "work"
        with self.assertRaisesRegex(RuntimeError, "离开"):
            self.backend.assert_background()

    def test_grid_copy_rejects_foreign_pid(self):
        self.discover()
        self.nodes[4]["pid"] = 99
        with self.assertRaises(RuntimeError):
            self.backend.copy_grid(4)
        self.native.post_message.assert_not_called()

    def test_grid_copy_is_a_command_not_a_mouse_or_keyboard_event(self):
        self.discover()
        self.backend.copy_grid(self.backend.grid())
        self.native.post_message.assert_called_once_with(
            4, module.WM_COMMAND, module.GRID_COPY_COMMAND, 0
        )

    def test_clipboard_owner_is_preserved_and_only_read(self):
        self.native.clipboard_owner.return_value = 2
        self.assertEqual(self.backend.clipboard_owner_pid(), 20)
        self.native.post_message.assert_not_called()

    def test_recycled_window_identity_is_rejected(self):
        self.discover()
        self.nodes[2]["pid"] = 21
        self.assertFalse(self.backend.identity_ok())

    def test_wrong_client_path_does_not_enter_or_move(self):
        with self.assertRaisesRegex(RuntimeError, "路径"):
            self.backend.discover(r"C:\other\xiadan.exe")
        self.desktops.create.assert_not_called()
        self.desktops.move.assert_not_called()


class DesktopCreationTests(unittest.TestCase):
    def test_rename_failure_does_not_leak_the_new_empty_desktop(self):
        current = SimpleNamespace(id="work")
        desktop = SimpleNamespace(
            id="owned",
            rename=mock.Mock(side_effect=RuntimeError("rename")),
            apps_by_z_order=mock.Mock(return_value=[]),
            remove=mock.Mock(),
        )
        api = object.__new__(module._Desktops)
        api._pyvda = SimpleNamespace(
            VirtualDesktop=SimpleNamespace(
                create=lambda: desktop,
                current=lambda: current,
            )
        )
        with self.assertRaisesRegex(RuntimeError, "rename"):
            api.create("background")
        desktop.remove.assert_called_once_with(fallback=current)


if __name__ == "__main__":
    unittest.main()
