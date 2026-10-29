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
        # 记录是否有人动过“全撤”，用于断言单笔撤单绝不会牵连全撤。
        self.cancel_all_calls = 0

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

    def test_trade_aborts_when_foreground_channel_unavailable(self):
        """下单走前台通道：拿不到通道时必须在提交前中止。

        早先 trade() 直接调 _submit_trade；现在改为前台通道填表+提交，
        通道不可用时绝不能退回到会被静默忽略的 WM_SETTEXT 路径，
        更不能在没比对确认弹窗的情况下提交。
        """
        trader = MinimalTrader()
        trader.is_exist_pop_dialog = lambda: False
        trader._trade_channel = mock.Mock(
            side_effect=exceptions.TradeError("前台通道不可用")
        )
        with self.assertRaises(exceptions.TradeError):
            trader.trade("600000", 10.0, 100)
        self.assertEqual(trader.submitted, 0)

    def test_limit_order_aborts_before_fill_when_baseline_unavailable(self):
        """读不到委托基线时，必须在填表/提交之前中止。

        没有基线就无法判断哪一笔是本次新增的，硬下单只能靠猜测认领编号，
        进而拿错编号去撤单。
        """
        trader = MinimalTrader()
        channel = mock.MagicMock()
        trader._trade_channel = mock.Mock(return_value=channel)
        trader._entrust_numbers = mock.Mock(return_value=None)
        trader._set_trade_params = mock.Mock()

        with self.assertRaises(exceptions.TradeError) as caught:
            trader.trade("600000", 10.0, 100, menu_path=["买入[F1]"])

        self.assertIn("基线", str(caught.exception))
        trader._set_trade_params.assert_not_called()
        channel.submit.assert_not_called()
        self.assertEqual(trader.submitted, 0)


class AwaitNewEntrustTests(unittest.TestCase):
    """回查委托列表必须唯一命中，且拒绝废单/失败行。"""

    def _trader(self, rows):
        class BookTrader(MinimalTrader):
            @property
            def today_entrusts(self):
                return rows

        trader = BookTrader()
        trader._solve_captcha_if_any = lambda: False
        trader.wait = lambda seconds: None
        return trader

    ROWS = [
        {"合同编号": "100", "证券代码": "600000", "委托数量": 100, "备注": "已报"},
        {"合同编号": "101", "证券代码": "600000", "委托数量": 100, "备注": "已报"},
    ]

    def test_none_baseline_is_rejected_even_when_called_directly(self):
        """私有方法被直接调用时也不能拿 None 基线去猜。"""
        trader = self._trader(self.ROWS)
        with self.assertRaises(exceptions.TradeError) as caught:
            trader._await_new_entrust("600000", 100, None, timeout=0.1)
        self.assertIn("基线", str(caught.exception))

    def test_exactly_one_new_row_is_returned(self):
        trader = self._trader(self.ROWS)
        result = trader._await_new_entrust("600000", 100, {"100"}, timeout=0.2)
        self.assertEqual(result["entrust_no"], "101")

    def test_two_candidates_abort_instead_of_picking_first(self):
        """多个新候选时必须报结果未知，绝不能返回 matches[0]。"""
        trader = self._trader(self.ROWS)
        with self.assertRaises(exceptions.TradeError) as caught:
            trader._await_new_entrust("600000", 100, set(), timeout=0.2)
        self.assertIn("未知", str(caught.exception))

    def test_rejected_rows_are_never_returned(self):
        """新出现的废单/失败行不是有效委托，不能把它的编号交出去。"""
        for status in ("废单", "失败", "已撤"):
            with self.subTest(status=status):
                rows = [
                    {"合同编号": "200", "证券代码": "600000",
                     "委托数量": 100, "备注": status},
                ]
                trader = self._trader(rows)
                with self.assertRaises(exceptions.TradeError):
                    trader._await_new_entrust("600000", 100, {"1"}, timeout=0.2)

    def test_fractional_quantity_is_not_truncated_to_match(self):
        """100.9 不能被 int(float()) 截断成 100 而误认成本次委托。"""
        rows = [
            {"合同编号": "300", "证券代码": "600000",
             "委托数量": "100.9", "备注": "已报"},
        ]
        trader = self._trader(rows)
        with self.assertRaises(exceptions.TradeError):
            trader._await_new_entrust("600000", 100, {"1"}, timeout=0.2)

    def test_row_missing_code_column_is_not_a_match(self):
        """行里没有证券代码时按“不匹配”处理，不做空值放宽。"""
        rows = [{"合同编号": "400", "委托数量": 100, "备注": "已报"}]
        trader = self._trader(rows)
        with self.assertRaises(exceptions.TradeError):
            trader._await_new_entrust("600000", 100, {"1"}, timeout=0.2)


class MarketTradeTypeFailure(unittest.TestCase):
    def test_aborts_before_submit_when_type_not_selectable(self):
        """市价类型选不上时必须中止，不能按客户端残留选项下单。"""
        trader = MinimalTrader()
        trader._type_edit_control_keys = lambda *a, **k: None
        trader._set_market_trade_params = lambda *a, **k: None
        trader._set_market_trade_type = mock.Mock(side_effect=TypeError("no such type"))

        with self.assertRaises(exceptions.TradeError):
            trader._market_trade_original("600000", 100, ttype="对手方最优价格")

        self.assertEqual(trader.submitted, 0)


class MarketTradeDisabledTests(unittest.TestCase):
    """同花顺的市价委托在无离线可验证比对方式前必须失败关闭。"""

    def test_market_trade_refuses_before_any_side_effect(self):
        trader = MinimalTrader()
        self.assertEqual(trader.broker_type, "ths")
        touched = []
        trader._type_edit_control_keys = lambda *a, **k: touched.append("field")
        trader._set_market_trade_params = lambda *a, **k: touched.append("params")

        with self.assertRaises(exceptions.TradeError) as caught:
            trader.market_trade("600000", 100)
        self.assertIn("市价委托", str(caught.exception))
        self.assertEqual(touched, [])
        self.assertEqual(trader.submitted, 0)

    def test_market_buy_and_sell_do_not_reach_submit(self):
        for name in ("market_buy", "market_sell"):
            with self.subTest(method=name):
                trader = MinimalTrader()
                trader._switch_left_menus = lambda *a, **k: None
                trader._type_edit_control_keys = lambda *a, **k: None
                trader._set_market_trade_params = lambda *a, **k: None
                trader._submit_trade = mock.Mock()

                with self.assertRaises(exceptions.TradeError):
                    getattr(trader, name)("600000", 100)
                trader._submit_trade.assert_not_called()

    def test_other_brokers_keep_their_market_path(self):
        """其他券商适配器不能被 ths 的失败关闭牵连。"""

        class OtherTrader(MinimalTrader):
            @property
            def broker_type(self):
                return "ht"

        trader = OtherTrader()
        trader._type_edit_control_keys = lambda *a, **k: None
        trader._set_market_trade_params = lambda *a, **k: None
        trader._submit_trade = mock.Mock()
        trader._handle_pop_dialogs = mock.Mock(return_value={"entrust_no": "42"})

        result = trader.market_trade("600000", 100)
        self.assertEqual(result, {"entrust_no": "42"})
        trader._submit_trade.assert_called_once()



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


class FakeBookTrader(MinimalTrader):
    """用可注入的表格替换真实属性，避免触碰任何真实窗口。"""

    book = []
    entrusts_after = []

    def __init__(self):
        super().__init__()
        self.cancel_all_calls = 0

    @property
    def cancel_entrusts(self):
        return list(self.book)

    @property
    def today_entrusts(self):
        return list(self.entrusts_after)

    def cancel_all_entrusts(self):
        # 单笔撤单路径一旦走到这里，就是又退回了“撤一笔变全撤”的旧缺陷。
        self.cancel_all_calls += 1
        raise AssertionError("单笔撤单不允许调用 cancel_all_entrusts")


class CancelSingleOnlyTests(unittest.TestCase):
    """单笔撤单绝不能化简成“全撤”。

    曾经的缺陷：``cancel_entrust`` 内部直接调用 ``cancel_all_entrusts()``，
    “撤一笔”实际撤掉了账户里所有挂单。下面的用例把这条红线钉死：
    任何路径下都不得触发全撤。
    """

    ROWS = [
        {"合同编号": "111", "证券代码": "600000", "委托数量": 100, "备注": ""},
        {"合同编号": "222", "证券代码": "000001", "委托数量": 200, "备注": ""},
    ]

    def _trader(self, rows=None, after=None):
        trader = FakeBookTrader()
        trader.book = list(self.ROWS if rows is None else rows)
        trader.entrusts_after = list(
            [
                {"合同编号": "111", "证券代码": "600000", "委托数量": 100, "备注": "已撤"},
                {"合同编号": "222", "证券代码": "000001", "委托数量": 200, "备注": ""},
            ]
            if after is None
            else after
        )
        trader._switch_left_menus = lambda *a, **k: None
        trader.refresh = lambda: None
        return trader

    def test_never_calls_cancel_all_when_target_exists(self):
        trader = self._trader()
        with self.assertRaises(exceptions.TradeError):
            trader.cancel_entrust("111")
        self.assertEqual(trader.cancel_all_calls, 0)

    def test_never_calls_cancel_all_for_unknown_entrust_no(self):
        trader = self._trader()
        with self.assertRaises(exceptions.TradeError):
            trader.cancel_entrust("999")
        self.assertEqual(trader.cancel_all_calls, 0)

    def test_never_calls_cancel_all_on_empty_book(self):
        trader = self._trader(rows=[])
        with self.assertRaises(exceptions.TradeError):
            trader.cancel_entrust("111")
        self.assertEqual(trader.cancel_all_calls, 0)

    def test_unknown_entrust_no_has_no_cancel_side_effect(self):
        """编号不存在时只报错，不点任何撤单按钮。"""
        trader = self._trader()
        trader._cancel_single_row = mock.Mock()
        with self.assertRaises(exceptions.TradeError) as caught:
            trader.cancel_entrust("999")
        self.assertIn("未执行撤单", str(caught.exception))
        trader._cancel_single_row.assert_not_called()

    def test_empty_entrust_no_is_rejected(self):
        trader = self._trader()
        with self.assertRaises(exceptions.TradeError):
            trader.cancel_entrust("   ")

    def test_unsupported_targeted_cancel_fails_closed_with_explanation(self):
        """单笔入口未验证时必须明确报“不支持”，且不产生撤单副作用。"""
        trader = self._trader()
        with self.assertRaises(exceptions.TradeError) as caught:
            trader.cancel_entrust("111")
        message = str(caught.exception)
        self.assertIn("尚未验证", message)
        self.assertIn("人工", message)

    def test_failure_message_does_not_claim_only_all_cancel_exists(self):
        """撤单页有“撤单(Del)”按钮，报错文案不能谎称客户端只有“全撤”。"""
        trader = self._trader()
        with self.assertRaises(exceptions.TradeError) as caught:
            trader.cancel_entrust("111")
        self.assertNotIn("只提供", str(caught.exception))
        self.assertIn("单行", str(caught.exception))

    def test_successful_single_cancel_reports_only_target(self):
        """真正执行单笔撤单后，只回报目标委托，且不牵连其它行。"""
        trader = self._trader()
        trader._cancel_single_row = mock.Mock()
        result = trader.cancel_entrust("111")
        self.assertEqual(result["entrust_no"], "111")
        self.assertEqual(result["status"], "已撤")
        trader._cancel_single_row.assert_called_once()
        # 只针对目标行下标调用，不传整张表
        args = trader._cancel_single_row.call_args[0]
        self.assertEqual(args[0], 0)
        self.assertEqual(args[1], "111")

    def test_second_row_targets_right_index(self):
        """目标在第二行时也必须定位到第 2 行，不能永远撤第一行。"""
        trader = self._trader(after=[
            {"合同编号": "111", "证券代码": "600000", "委托数量": 100, "备注": ""},
            {"合同编号": "222", "证券代码": "000001", "委托数量": 200, "备注": "已撤"},
        ])
        trader._cancel_single_row = mock.Mock()
        result = trader.cancel_entrust("222")
        self.assertEqual(result["entrust_no"], "222")
        trader._cancel_single_row.assert_called_once_with(1, "222")

    def test_collateral_cancellation_is_detected(self):
        """若撤单牵连了别的委托（挂单数量异常减少），必须报错而不是报成功。"""
        trader = self._trader(after=[
            {"合同编号": "111", "证券代码": "600000", "委托数量": 100, "备注": "已撤"},
        ])
        trader._cancel_single_row = mock.Mock()
        with self.assertRaises(exceptions.TradeError) as caught:
            trader.cancel_entrust("111")
        self.assertIn("牵连", str(caught.exception))

    def test_target_not_cancelled_is_reported_honestly(self):
        """撤单后目标仍是“已报”时不能报成功。"""
        trader = self._trader(after=[
            {"合同编号": "111", "证券代码": "600000", "委托数量": 100, "备注": "已报"},
            {"合同编号": "222", "证券代码": "000001", "委托数量": 200, "备注": ""},
        ])
        trader._cancel_single_row = mock.Mock()
        with self.assertRaises(exceptions.TradeError) as caught:
            trader.cancel_entrust("111")
        self.assertIn("已报", str(caught.exception))

    def test_pending_status_never_returned_as_success(self):
        """未报/已报等活动状态一律不得作为成功状态返回。"""
        for status in ("未报", "已报", "部成", "部分成交", "待报", "正报"):
            with self.subTest(status=status):
                trader = self._trader(after=[
                    {"合同编号": "111", "证券代码": "600000", "委托数量": 100,
                     "备注": status},
                    {"合同编号": "222", "证券代码": "000001", "委托数量": 200,
                     "备注": ""},
                ])
                trader._cancel_single_row = mock.Mock()
                with self.assertRaises(exceptions.TradeError):
                    trader.cancel_entrust("111")


if __name__ == "__main__":
    unittest.main()
