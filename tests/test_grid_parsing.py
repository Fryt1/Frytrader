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

        editor.set_focus.assert_called()
        editor.set_edit_text.assert_not_called()

        typed = [call.args[0] for call in editor.type_keys.call_args_list]
        self.assertIn("Ab3d", typed)
        # 必须最后输入验证码本身
        self.assertEqual(typed[-1], "Ab3d")

    def test_stale_captcha_text_is_backspaced_away(self):
        """重试前必须真正清空输入框。

        实测 Ctrl+A / Delete 在该控件上无效，只有 BACKSPACE 能删除。
        如果不清空，重试会拼成 "R2kuR2ku" 之类的 8 位内容，永远无法通过。
        """
        editor = mock.MagicMock()
        editor.type_keys.side_effect = lambda keys, **kwargs: keys

        grid_strategies.BaseStrategy._fill_captcha(editor, "Wq7z")

        typed = [call.args[0] for call in editor.type_keys.call_args_list]
        self.assertGreaterEqual(
            typed.count("{BACKSPACE}"), 4, "退格次数应足以清掉旧内容"
        )
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


if __name__ == "__main__":
    unittest.main()
