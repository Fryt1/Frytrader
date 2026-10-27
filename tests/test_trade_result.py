# -*- coding: utf-8 -*-
"""下单结果与弹窗处理的离线回归测试。

对应实盘风险：
- 拿到弹窗结果但没有委托编号，却被当成下单成功
- 无法识别的弹窗既不关闭也不返回，导致主流程死循环
- 市价类型选择失败后仍然继续提交，按客户端残留选项下单
"""

import unittest
from unittest import mock

from easytrader import clienttrader, exceptions, pop_dialog_handler


class MinimalTrader(clienttrader.ClientTrader):
    """绕过 GUI 初始化，只测试下单结果判定逻辑。"""

    def __init__(self):  # noqa: D107 - 刻意不调用父类构造
        self._config = clienttrader.client.create("ths")
        self._app = mock.MagicMock()
        self._main = mock.MagicMock()
        self._toolbar = None
        self._grid_strategy_instance = None
        self.dialog_titles = []
        self.handled = []
        self.closed = False
        self.submitted = 0

    def wait(self, seconds):
        return None

    def is_exist_pop_dialog(self):
        return bool(self.dialog_titles)

    def _get_pop_dialog_title(self):
        return self.dialog_titles.pop(0) if self.dialog_titles else ""

    def close_pop_dialog(self):
        self.closed = True
        self.dialog_titles = []

    def _set_trade_params(self, security, price, amount):
        return None

    def _submit_trade(self):
        self.submitted += 1


class FakeHandler:
    def __init__(self, app):
        self._app = app
        self.calls = []

    def handle(self, title):
        self.calls.append(title)
        # 模拟“永远处理不完”的弹窗
        return None


class TestHandlePopDialogs(unittest.TestCase):
    def test_returns_none_when_no_dialog_appeared(self):
        trader = MinimalTrader()
        self.assertIsNone(trader._handle_pop_dialogs())

    def test_unhandled_dialog_does_not_loop_forever(self):
        """无法收敛的弹窗必须报错并关闭，不能无限循环或静默成功。"""
        trader = MinimalTrader()
        # 每次检查都还有弹窗
        trader.is_exist_pop_dialog = lambda: True
        trader._get_pop_dialog_title = lambda: "未知弹窗"

        with self.assertRaises(exceptions.TradeError):
            trader._handle_pop_dialogs(handler_class=FakeHandler)
        self.assertTrue(trader.closed)

    def test_handler_result_is_returned(self):
        trader = MinimalTrader()
        trader.dialog_titles = ["提示"]

        class Handler(FakeHandler):
            def handle(self, title):
                self.calls.append(title)
                return {"entrust_no": "12345"}

        result = trader._handle_pop_dialogs(handler_class=Handler)
        self.assertEqual(result, {"entrust_no": "12345"})


class TestRequireEntrustResult(unittest.TestCase):
    def test_entrust_no_passes_through(self):
        result = {"entrust_no": "888"}
        self.assertEqual(
            MinimalTrader._require_entrust_result(result), result
        )

    def test_none_result_raises(self):
        """弹窗消失但没有委托编号，不能当作成功。"""
        with self.assertRaises(exceptions.TradeError):
            MinimalTrader._require_entrust_result(None)

    def test_empty_dict_raises(self):
        with self.assertRaises(exceptions.TradeError):
            MinimalTrader._require_entrust_result({})

    def test_message_only_dict_raises(self):
        with self.assertRaises(exceptions.TradeError):
            MinimalTrader._require_entrust_result({"message": "success"})

    def test_trade_raises_when_no_entrust_no(self):
        trader = MinimalTrader()
        trader.is_exist_pop_dialog = lambda: False
        with self.assertRaises(exceptions.TradeError):
            trader.trade("600000", 10.0, 100)
        self.assertEqual(trader.submitted, 1)


class TestMarketTradeTypeFailure(unittest.TestCase):
    def test_aborts_before_submit_when_type_not_selectable(self):
        """市价类型选不上时必须中止，不能按客户端残留选项下单。"""
        trader = MinimalTrader()
        trader._type_edit_control_keys = lambda *a, **k: None
        trader._set_market_trade_params = lambda *a, **k: None
        trader._set_market_trade_type = mock.Mock(side_effect=TypeError("no such type"))

        with self.assertRaises(exceptions.TradeError):
            trader.market_trade("600000", 100, ttype="对手方最优价格")

        self.assertEqual(trader.submitted, 0)


class TestTradePopDialogHandler(unittest.TestCase):
    def setUp(self):
        self.app = mock.MagicMock()
        self.handler = pop_dialog_handler.TradePopDialogHandler(self.app)
        self.handler._submit_by_shortcut = mock.Mock()
        self.handler._submit_by_click = mock.Mock()
        self.handler._close = mock.Mock()

    def _set_content(self, text):
        self.handler._extract_content = mock.Mock(return_value=text)

    def test_success_returns_entrust_no(self):
        self._set_content("委托成功，合同编号：12345")
        result = self.handler.handle("提示")
        self.assertEqual(result, {"entrust_no": "12345"})
        self.handler._submit_by_click.assert_called()

    def test_success_without_entrust_no_raises(self):
        """提示成功却解析不到委托编号时，必须报错而不是返回空成功。"""
        self.handler._extract_content = mock.Mock(return_value="委托成功")
        self.handler._extract_entrust_id = mock.Mock(return_value=None)
        # 正则对“委托成功”能匹配到“委托”中的字母？中文不含 \d\w 的 ASCII，
        # 因此 match 为 None，应触发异常
        with self.assertRaises(exceptions.TradeError):
            self.handler.handle("提示")

    def test_failure_message_raises_trade_error(self):
        self._set_content("资金不足")
        with self.assertRaises(exceptions.TradeError):
            self.handler.handle("提示")

    def test_unknown_info_dialog_is_closed_and_reported(self):
        """无法识别的“提示信息”必须关闭并回报，不能让上层反复遇到同一个弹窗。"""
        self._set_content("某个没见过的提示")
        result = self.handler.handle("提示信息")
        self.handler._close.assert_called_once()
        self.assertEqual(result, {"message": "某个没见过的提示"})

    def test_unknown_dialog_is_closed_and_reported(self):
        self._set_content("莫名其妙")
        result = self.handler.handle("奇怪窗口")
        self.handler._close.assert_called_once()
        self.assertIn("unknown", result["message"])


if __name__ == "__main__":
    unittest.main()
