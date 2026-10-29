# -*- coding: utf-8 -*-
"""表格解析与验证码处理的离线回归测试。

覆盖在同花顺下单程序上实测到的缺陷：
- 空表格被解析出全空占位行（假委托记录）
- 多个同 control_id 的表格控件导致 ElementAmbiguousError 后静默返回 None
- 解析失败时静默返回 None
- 验证码输入框不响应 set_edit_text，必须模拟键盘输入
- 复制失败时读到剪贴板里的陈旧数据
"""

import io
import unittest
from unittest import mock

import pandas as pd

from easytrader import grid_strategies
from easytrader.config import client as client_config
from easytrader.utils import captcha as captcha_utils

GRID_DTYPE = client_config.create("ths").GRID_DTYPE

# 客户端在“当日委托”无数据时导出的真实内容：只有表头 + 两行全 0 占位行
EMPTY_ENTRUST_TSV = (
    "委托时间\t证券代码\t证券名称\t操作\t备注\t委托数量\t成交数量\t委托价格\t"
    "成交均价\t撤消数量\t合同编号\t交易市场\t\r\n"
    "\t\t\t\t\t0\t0\t0.000\t0.000\t0\t\t\t\r\n"
    "\t\t\t\t\t0\t0\t0.000\t0.000\t0\t\t\t"
)

# 有真实委托时：一行真实数据 + 一行占位行
ONE_ENTRUST_TSV = (
    "委托时间\t证券代码\t证券名称\t操作\t备注\t委托数量\t成交数量\t委托价格\t"
    "成交均价\t撤消数量\t合同编号\t交易市场\t\r\n"
    "10:00:00\t600000\t浦发银行\t买入\t\t100\t0\t10.000\t0.000\t0\t12345\t"
    "上海Ａ股\t\r\n"
    "\t\t\t\t\t0\t0\t0.000\t0.000\t0\t\t\t"
)


def parse_tsv(content):
    df = pd.read_csv(
        io.StringIO(content),
        delimiter="\t",
        dtype=GRID_DTYPE,
        na_filter=False,
    )
    return grid_strategies._drop_empty_rows(df)


class TestDropEmptyRows(unittest.TestCase):
    def test_empty_grid_yields_no_records(self):
        """空表格不能解析出全 0 的假委托记录。"""
        self.assertEqual(parse_tsv(EMPTY_ENTRUST_TSV), [])

    def test_placeholder_row_filtered_but_real_row_kept(self):
        records = parse_tsv(ONE_ENTRUST_TSV)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["证券代码"], "600000")
        self.assertEqual(records[0]["委托数量"], 100)

    def test_zero_valued_real_row_is_not_dropped(self):
        """真实委托的成交数量/成交均价本来就是 0，不能因此被误删。"""
        records = grid_strategies._drop_empty_rows(
            pd.DataFrame([{"证券代码": "600000", "委托数量": 0, "委托价格": 0.0}])
        )
        self.assertEqual(len(records), 1)

    def test_empty_dataframe_returns_empty_list(self):
        self.assertEqual(grid_strategies._drop_empty_rows(pd.DataFrame()), [])


class FakeGrid:
    def __init__(self, control_id, visible):
        self._control_id = control_id
        self._visible = visible

    def class_name(self):
        return "CVirtualGridCtrl"

    def control_id(self):
        return self._control_id

    def is_visible(self):
        return self._visible


class FakeTrader:
    """用于测试 _get_grid / 验证码流程的最小 trader 替身。"""

    def __init__(self, grids):
        self._grids = grids

    @property
    def main(self):
        trader = self

        class Main:
            @staticmethod
            def descendants():
                return trader._grids

        return Main()

    @property
    def config(self):
        return client_config.create("ths")

    def wait(self, seconds):
        return None


class TestGetGridSelection(unittest.TestCase):
    def setUp(self):
        self.strategy = grid_strategies.Copy()

    def test_picks_the_single_visible_grid(self):
        """客户端保留多个同 ID 表格控件时，应选中当前可见的那个。"""
        hidden1 = FakeGrid(1047, False)
        hidden2 = FakeGrid(1047, False)
        visible = FakeGrid(1047, True)
        self.strategy.set_trader(FakeTrader([hidden1, visible, hidden2]))
        self.assertIs(self.strategy._get_grid(1047), visible)

    def test_no_visible_grid_raises_instead_of_returning_none(self):
        self.strategy.set_trader(FakeTrader([FakeGrid(1047, False)]))
        with self.assertRaises(RuntimeError):
            self.strategy._get_grid(1047)

    def test_ambiguous_visible_grids_raise(self):
        self.strategy.set_trader(
            FakeTrader([FakeGrid(1047, True), FakeGrid(1047, True)])
        )
        with self.assertRaises(RuntimeError):
            self.strategy._get_grid(1047)

    def test_destroyed_controls_are_skipped(self):
        class Exploding(FakeGrid):
            def is_visible(self):
                raise RuntimeError("control destroyed")

        visible = FakeGrid(1047, True)
        self.strategy.set_trader(FakeTrader([Exploding(1047, True), visible]))
        self.assertIs(self.strategy._get_grid(1047), visible)


class TestFormatGridDataRaises(unittest.TestCase):
    def test_unparsable_content_raises_instead_of_returning_none(self):
        """解析失败必须抛错，不能返回 None 让调用方以为没数据。"""
        strategy = grid_strategies.Copy()
        strategy.set_trader(FakeTrader([]))
        # 表头与数据列数严重不匹配，pandas 会解析失败
        with self.assertRaises(Exception):
            strategy._format_grid_data('a\tb\n"unclosed')


class TestCaptchaInput(unittest.TestCase):
    """验证码输入框的两个硬约束（实机实测）。"""

    def test_captcha_is_typed_not_set_via_set_edit_text(self):
        """验证码框必须用 type_keys 模拟键盘输入。

        实测 set_edit_text 对该控件无效（写入后内容为空），提交必然被判验证码错误。
        """
        editor = mock.MagicMock()
        editor.type_keys.side_effect = lambda keys, **kwargs: keys

        grid_strategies.BaseStrategy._fill_captcha(editor, "Ab3d")

        editor.set_edit_text.assert_not_called()

        typed = [call.args[0] for call in editor.type_keys.call_args_list]
        self.assertIn("Ab3d", typed)
        # 必须最后输入验证码本身
        self.assertEqual(typed[-1], "Ab3d")

    def test_typing_never_lets_pywinauto_take_foreground(self):
        """type_keys 必须显式传 set_foreground=False。

        实测该环境下 pywinauto 内部的 SetForegroundWindow 必定抛
        "(0, 'SetForegroundWindow', 'No error message is available')"；
        一旦让它自己去置前，整个写入直接失败。前台与焦点由我们自己在
        AttachThreadInput 之后处理。
        """
        editor = mock.MagicMock()
        editor.type_keys.side_effect = lambda keys, **kwargs: keys

        grid_strategies.BaseStrategy._fill_captcha(editor, "Ab3d")

        for call in editor.type_keys.call_args_list:
            self.assertIs(
                call.kwargs.get("set_foreground"),
                False,
                "type_keys 必须传 set_foreground=False：%r" % (call,),
            )

    def test_focus_is_set_after_attaching_dialog_thread(self):
        """必须先挂上对话框线程再 SetFocus。

        实测直接 editor.set_focus() 之后 GetFocus() 仍是 0，说明焦点根本没
        进控件；只有 AttachThreadInput 之后 SetFocus 才生效。

        win32 模块是在 _fill_captcha 内部导入的，所以直接 patch 真正的
        win32gui / win32process 模块对象。
        """
        import win32gui as real_gui
        import win32process as real_process

        editor = mock.MagicMock()
        handle = 0x1234
        editor.wrapper_object.return_value.handle = handle
        editor.type_keys.side_effect = lambda keys, **kwargs: keys

        with mock.patch.object(
            real_gui, "GetAncestor", return_value=0x9999
        ), mock.patch.object(
            real_process,
            "GetWindowThreadProcessId",
            return_value=(4242, 1),
        ), mock.patch.object(
            real_process, "AttachThreadInput"
        ) as attach, mock.patch.object(
            real_gui, "SetFocus"
        ) as set_focus, mock.patch.object(
            real_gui, "SetForegroundWindow"
        ):
            grid_strategies.BaseStrategy._fill_captcha(editor, "Ab3d")

        attach.assert_called()
        # 挂线程必须成对（挂上再解挂）
        self.assertGreaterEqual(attach.call_count, 2)
        set_focus.assert_called_with(handle)

    def test_stale_captcha_text_is_backspaced_away(self):
        """重试前必须真正清空输入框。

        实测 Ctrl+A / Delete 在该控件上无效，只有 BACKSPACE 能删除。
        如果不清空，重试会拼成 "R2kuR2ku" 之类的 8 位内容，永远无法通过。
        退格合并成一次 type_keys（"{BACKSPACE 16}"）以减少按键往返开销。
        """
        editor = mock.MagicMock()
        editor.type_keys.side_effect = lambda keys, **kwargs: keys

        grid_strategies.BaseStrategy._fill_captcha(editor, "Wq7z")

        typed = [call.args[0] for call in editor.type_keys.call_args_list]
        # 必须有一次包含多次退格的调用
        backspace_calls = [k for k in typed if "BACKSPACE" in k.upper()]
        self.assertTrue(backspace_calls, "必须先清空旧内容")
        count = 0
        for keys in backspace_calls:
            digits = "".join(ch for ch in keys if ch.isdigit())
            count += int(digits) if digits else 1
        self.assertGreaterEqual(count, 4, "退格次数应足以清掉旧内容")
        # 不能依赖无效的 Ctrl+A / Delete
        self.assertNotIn("^a", typed)
        self.assertNotIn("{DELETE}", typed)
        # 清空动作必须发生在输入新验证码之前
        self.assertEqual(typed[-1], "Wq7z")

    def test_xls_strategy_shares_the_same_input_logic(self):
        """Copy 和 Xls 必须共用同一份验证码输入实现。

        早期两份实现不一致：Xls 用无效的 Ctrl+A+Delete 清空，
        导致重试时内容累积、验证码永远失败。
        """
        self.assertIs(
            grid_strategies.Xls._fill_captcha,
            grid_strategies.BaseStrategy._fill_captcha,
        )
        self.assertIs(
            grid_strategies.Copy._fill_captcha,
            grid_strategies.BaseStrategy._fill_captcha,
        )


class TestCaptchaSolve(unittest.TestCase):
    def setUp(self):
        self.strategy = grid_strategies.Copy()
        self.strategy.set_trader(FakeTrader([]))
        self.strategy.wait = lambda *_: None

    def test_no_dialog_means_pass(self):
        with mock.patch.object(self.strategy, "_wait_captcha_dialog", return_value=None):
            self.assertTrue(self.strategy._solve_captcha())

    def test_unrecognized_captcha_is_refreshed_without_submitting(self):
        """识别不出结果时不能提交空值，应直接换一张验证码。"""
        dialog = mock.MagicMock()
        dialog.window.return_value = mock.MagicMock()

        state = {"refreshed": 0}

        def fake_refresh(_):
            state["refreshed"] += 1

        def fake_get_dialog():
            # 第一次返回弹窗，刷新后认为已通过
            return dialog if state["refreshed"] == 0 else None

        submitted_ids = []

        def record_window(**kwargs):
            submitted_ids.append(kwargs.get("control_id"))
            return mock.MagicMock()

        dialog.window.side_effect = record_window

        with mock.patch.object(self.strategy, "_wait_captcha_dialog", return_value=dialog), \
             mock.patch.object(self.strategy, "_get_captcha_dialog", side_effect=fake_get_dialog), \
             mock.patch.object(self.strategy, "_set_foreground"), \
             mock.patch.object(self.strategy, "_refresh_captcha", side_effect=fake_refresh), \
             mock.patch.object(
                 self.strategy, "_capture_control", return_value=mock.MagicMock()
             ), \
             mock.patch.object(
                 grid_strategies, "captcha_recognize_improved", return_value=""
             ):
            self.assertTrue(self.strategy._solve_captcha())

        self.assertEqual(state["refreshed"], 1)
        # 没有识别结果时不应点击“确定”（control_id=1）提交
        self.assertNotIn(
            grid_strategies.Copy.CAPTCHA_OK_BUTTON_ID, submitted_ids
        )


class TestClipboardFailure(unittest.TestCase):
    def setUp(self):
        self.strategy = grid_strategies.Copy()
        self.strategy.set_trader(FakeTrader([]))
        self.strategy.wait = lambda *_: None

    def test_clipboard_failure_raises_and_does_not_cache_stale_data(self):
        with mock.patch.object(self.strategy, "_solve_captcha", return_value=True), \
             mock.patch.object(
                 grid_strategies.pywinauto.clipboard,
                 "GetData",
                 side_effect=RuntimeError("That format is not available"),
             ):
            with self.assertRaises(RuntimeError):
                self.strategy._get_clipboard_data()

    def test_solve_failure_raises(self):
        with mock.patch.object(self.strategy, "_solve_captcha", return_value=False):
            with self.assertRaises(RuntimeError):
                self.strategy._get_clipboard_data()


class TestCaptchaVoting(unittest.TestCase):
    """验证码结果聚合：按位置投票，大小写单独投票。

    真实样本里 tesseract 的大小写判断比字符本身更不稳定，
    整串比较会让多数投票永远选不出结果，因此拆成两个维度投票。
    """

    def test_majority_character_wins_per_position(self):
        result = captcha_utils.select_captcha_by_position(
            ["Abcd", "Abcd", "Abx d".replace(" ", "")]
        )
        self.assertEqual(result, "Abcd")

    def test_case_is_voted_separately(self):
        # 字符一致但大小写不同时，按大写/小写票数决定
        result = captcha_utils.select_captcha_by_position(
            ["ECat", "Ecat", "ECat"]
        )
        self.assertEqual(result, "ECat")

    def test_minority_case_character_still_kept(self):
        # 4 个候选里 3 个小写 -> 取小写，但字符本身不受影响
        result = captcha_utils.select_captcha_by_position(
            ["ecat", "ecat", "ecat", "ECat"]
        )
        self.assertEqual(result, "ecat")

    def test_empty_candidates_returns_empty(self):
        self.assertEqual(captcha_utils.select_captcha_by_position([]), "")
        self.assertEqual(
            captcha_utils.select_captcha_by_position(["", "ab"]), ""
        )

    def test_wrong_length_candidates_ignored(self):
        result = captcha_utils.select_captcha_by_position(["Abcd", "xyz"])
        self.assertEqual(result, "Abcd")


if __name__ == "__main__":
    unittest.main()
