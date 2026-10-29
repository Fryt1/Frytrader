# -*- coding: utf-8 -*-
"""前台交易通道的离线测试。

全部离线：不连接任何真实客户端，不创建原生窗口句柄。

覆盖的实盘风险（都来自真实踩坑）：
- 交易字段读不回来，校验只能落在"委托确认"弹窗的明文上；
- 确认弹窗内容与请求不一致时绝不能提交；
- ``today_entrusts`` 返回 list 而不是 DataFrame；
- 写字段必须走前台通道，不能退回会被静默忽略的 WM_SETTEXT。
"""

import unittest

from easytrader import exceptions
from easytrader.clienttrader import ClientTrader
from easytrader.foreground_trade import ConfirmSnapshot, _clean


CONFIRM_BODY = (
    "资金帐号：川财证券-郭*权\n"
    "证券代码：000001(平安银行)\n"
    '买入价格：<font  color=blue>10.390(跌停)</font>\n'
    "买入数量：100\n"
    "预估金额：1039.312\n"
    "\n"
    "您是否确定以上买入委托？"
)


class SnapshotParsingTests(unittest.TestCase):
    def test_parses_every_field_from_real_dialog_text(self):
        snapshot = ConfirmSnapshot(_clean(CONFIRM_BODY))

        self.assertEqual(snapshot.security, "000001")
        self.assertEqual(snapshot.name, "平安银行")
        self.assertEqual(snapshot.price, "10.390")
        self.assertEqual(snapshot.amount, "100")
        self.assertEqual(snapshot.total, "1039.312")
        self.assertEqual(snapshot.account, "川财证券-郭*权")

    def test_html_is_stripped_from_price(self):
        """客户端在价格里塞 <font> 标签，不清理会把价格解析成空。"""
        raw = _clean(CONFIRM_BODY)
        self.assertNotIn("<font", raw)
        self.assertIn("10.390(跌停)", raw)

    def test_sell_price_label_is_recognised(self):
        body = _clean(
            "证券代码：600000(浦发银行)\n"
            "卖出价格：9.880\n"
            "卖出数量：200\n"
        )
        snapshot = ConfirmSnapshot(body)
        self.assertEqual(snapshot.security, "600000")
        self.assertEqual(snapshot.price, "9.880")
        self.assertEqual(snapshot.amount, "200")

    def test_missing_fields_become_none(self):
        snapshot = ConfirmSnapshot("完全无法识别的文本")
        self.assertIsNone(snapshot.security)
        self.assertIsNone(snapshot.price)
        self.assertIsNone(snapshot.amount)


class VerifySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = ConfirmSnapshot(_clean(CONFIRM_BODY))

    def test_matching_order_passes(self):
        self.assertTrue(
            ClientTrader._verify_snapshot(self.snapshot, "000001", "10.390", 100)
        )

    def test_price_compared_numerically_not_textually(self):
        """客户端显示 10.390，请求传 10.39，数字上相等就不能误杀。"""
        self.assertTrue(
            ClientTrader._verify_snapshot(self.snapshot, "000001", "10.39", 100)
        )

    def test_wrong_security_aborts(self):
        with self.assertRaises(exceptions.TradeError) as caught:
            ClientTrader._verify_snapshot(self.snapshot, "600000", "10.390", 100)
        self.assertIn("证券代码", str(caught.exception))

    def test_wrong_amount_aborts(self):
        with self.assertRaises(exceptions.TradeError) as caught:
            ClientTrader._verify_snapshot(self.snapshot, "000001", "10.390", 200)
        self.assertIn("委托数量", str(caught.exception))

    def test_wrong_price_aborts(self):
        with self.assertRaises(exceptions.TradeError) as caught:
            ClientTrader._verify_snapshot(self.snapshot, "000001", "11.50", 100)
        self.assertIn("委托价格", str(caught.exception))

    def test_unparsable_snapshot_aborts_instead_of_passing(self):
        """弹窗解析失败时必须中止，绝不能当成"校验通过"去提交。"""
        with self.assertRaises(exceptions.TradeError):
            ClientTrader._verify_snapshot(
                ConfirmSnapshot(""), "000001", "10.390", 100
            )


class FrameRowsTests(unittest.TestCase):
    """today_entrusts 实测返回 list[dict]，早先按 DataFrame 写会直接崩。"""

    ROWS = [
        {"合同编号": "143", "证券代码": "000001", "委托数量": 100, "备注": "已撤(买卖)"}
    ]

    def test_list_of_dicts_is_supported(self):
        columns, rows = ClientTrader._frame_rows(self.ROWS, {"合同编号"})
        self.assertIn("合同编号", columns)
        self.assertEqual(rows, self.ROWS)

    def test_missing_required_column_returns_no_rows(self):
        _, rows = ClientTrader._frame_rows([{"无关": 1}], {"合同编号"})
        self.assertEqual(rows, [])

    def test_none_frame_is_empty(self):
        self.assertEqual(ClientTrader._frame_rows(None), (set(), []))

    def test_dataframe_is_supported(self):
        try:
            import pandas as pd
        except ImportError:  # pragma: no cover
            self.skipTest("pandas 未安装")
        frame = pd.DataFrame(self.ROWS)
        columns, rows = ClientTrader._frame_rows(frame, {"合同编号"})
        self.assertIn("合同编号", columns)
        self.assertEqual(rows[0]["合同编号"], "143")


class FieldWriteRoutingTests(unittest.TestCase):
    """写字段必须走前台通道，不能退回被静默忽略的 set_edit_text。"""

    def test_delegates_to_foreground_channel(self):
        class FakeChannel:
            def __init__(self):
                self.calls = []

            def type_field(self, control_id, text):
                self.calls.append((control_id, text))

        trader = ClientTrader.__new__(ClientTrader)
        channel = FakeChannel()
        trader._channel = channel

        trader._type_edit_control_keys(1032, "000001")

        self.assertEqual(channel.calls, [(1032, "000001")])


if __name__ == "__main__":
    unittest.main()
