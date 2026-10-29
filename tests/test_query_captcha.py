"""Offline safety checks for foreground QUERY-captcha input only."""
import unittest
from types import SimpleNamespace
from unittest import mock

from easytrader.background_runtime import BackgroundError
from easytrader.query_captcha import ForegroundCaptchaControls


class CaptchaInputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.backend = mock.Mock()
        self.backend.trader_pid = 77
        self.backend.check_captcha.return_value = True
        self.backend._native.return_value.thread_id.return_value = 101
        self.controls = SimpleNamespace(dialog=10, editor=11, image=12, ok=13, cancel=14)
        self.guard = mock.Mock()
        self.runtime = ForegroundCaptchaControls(self.backend)

    def input_patches(self, focus=11, foreground=10):
        import contextlib
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch('win32api.GetCurrentThreadId', return_value=202))
        attach = stack.enter_context(mock.patch('win32process.AttachThreadInput'))
        stack.enter_context(mock.patch('win32gui.SetForegroundWindow'))
        stack.enter_context(mock.patch('win32gui.SetFocus'))
        stack.enter_context(mock.patch('win32gui.GetForegroundWindow', return_value=foreground))
        stack.enter_context(mock.patch('win32gui.GetFocus', return_value=focus))
        stack.enter_context(mock.patch('easytrader.query_captcha.time.sleep'))
        send = stack.enter_context(mock.patch('pywinauto.keyboard.send_keys'))
        return attach, send

    def test_clears_before_typing_and_detaches_after_input(self):
        attach, send = self.input_patches()
        self.runtime.fill(self.controls, 'Ab3d', 77, self.guard)
        self.assertEqual(send.call_args_list[0].args[0], '{END}{BACKSPACE 16}')
        self.assertEqual([call.args[0] for call in send.call_args_list[1:]], list('Ab3d'))
        self.assertEqual(attach.call_args_list, [mock.call(202, 101, True), mock.call(202, 101, False)])
        self.backend.begin_captcha_input.assert_called_once_with(self.controls)

    def test_no_keys_when_focus_is_not_the_captcha_editor(self):
        attach, send = self.input_patches(focus=999)
        with self.assertRaisesRegex(BackgroundError, '前台与焦点'):
            self.runtime.fill(self.controls, 'Ab3d', 77, self.guard)
        send.assert_not_called()
        attach.assert_any_call(202, 101, False)

    def test_no_keys_when_foreground_belongs_elsewhere(self):
        _, send = self.input_patches(foreground=999)
        with self.assertRaises(BackgroundError):
            self.runtime.fill(self.controls, 'Ab3d', 77, self.guard)
        send.assert_not_called()

    def test_foreign_pid_rejected_before_activation(self):
        _, send = self.input_patches()
        with self.assertRaises(BackgroundError):
            self.runtime.fill(self.controls, 'Ab3d', 88, self.guard)
        send.assert_not_called()
        self.backend.begin_captcha_input.assert_not_called()

    def test_failed_identity_check_mid_input_stops_further_characters(self):
        attach, send = self.input_patches()
        self.backend.check_captcha.side_effect = [True, True, False]
        with self.assertRaises(BackgroundError):
            self.runtime.fill(self.controls, 'Ab3d', 77, self.guard)
        self.assertEqual([call.args[0] for call in send.call_args_list], ['{END}{BACKSPACE 16}'])
        attach.assert_any_call(202, 101, False)

    def test_unknown_action_never_posts_a_message(self):
        with self.assertRaises(BackgroundError):
            self.runtime.click(self.controls, 'trade_confirm', 77, self.guard)
        self.backend._native.return_value.post_message.assert_not_called()

    def test_confirm_targets_only_query_captcha_ok_button(self):
        self.runtime.click(self.controls, 'ok', 77, self.guard)
        self.backend._native.return_value.post_message.assert_called_once_with(13, 0x00F5)


if __name__ == '__main__':
    unittest.main()
