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
from easytrader.foreground_trade import (
    ConfirmSnapshot,
    ForegroundTradeChannel,
    _clean,
)


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


class EntrustNoExtractionTests(unittest.TestCase):
    """委托编号必须解析成**纯数字**，且不得从任意正文里捞数字。

    早先 ``confirm`` 用 ``match.group(0)``，返回值是“合同编号：123456”，
    上层再拿它去和委托列表的编号比对永远不相等，撤单必然失败。
    """

    def test_returns_bare_numeric_id(self):
        self.assertEqual(
            ForegroundTradeChannel._extract_entrust_no("委托成功，合同编号：123456"),
            "123456",
        )

    def test_short_three_digit_id_is_accepted(self):
        """合同编号不保证 4 位以上，3 位也要认。"""
        self.assertEqual(
            ForegroundTradeChannel._extract_entrust_no("合同编号:123"), "123"
        )

    def test_does_not_infer_from_arbitrary_digits(self):
        """正文里有价格/金额但没有编号字段时，绝不能抓一段数字当编号。"""
        for body in (
            "委托成功，成交金额 1234.56 元",
            "买入 000001 价格 10.39 数量 100",
            "资金余额 123456",
        ):
            with self.subTest(body=body):
                self.assertIsNone(
                    ForegroundTradeChannel._extract_entrust_no(body)
                )

    def test_empty_text_yields_none(self):
        self.assertIsNone(ForegroundTradeChannel._extract_entrust_no(""))


class DirectionVerificationTests(unittest.TestCase):
    """声明了方向就必须在弹窗里得到证明，读不到方向同样算校验失败。"""

    BUY_BODY = _clean(
        "证券代码：000001(平安银行)\n"
        "买入价格：10.390\n"
        "买入数量：100\n"
        "\n您是否确定以上买入委托？"
    )

    def test_direction_parsed_from_real_wording(self):
        snapshot = ConfirmSnapshot(self.BUY_BODY)
        self.assertEqual(snapshot.direction, "买入")

    def test_matching_direction_passes(self):
        snapshot = ConfirmSnapshot(self.BUY_BODY)
        self.assertTrue(ClientTrader._verify_snapshot_direction(snapshot, "买入"))

    def test_mismatched_direction_aborts(self):
        snapshot = ConfirmSnapshot(self.BUY_BODY)
        with self.assertRaises(exceptions.TradeError) as caught:
            ClientTrader._verify_snapshot_direction(snapshot, "卖出")
        self.assertIn("方向", str(caught.exception))

    def test_unreadable_direction_aborts_when_declared(self):
        """弹窗读不出方向时不能放行——无法证明的委托不许提交。"""
        snapshot = ConfirmSnapshot(_clean("证券代码：000001\n买入价格：10.390\n"))
        self.assertIsNone(snapshot.direction)
        with self.assertRaises(exceptions.TradeError):
            ClientTrader._verify_snapshot_direction(snapshot, "买入")

    def test_no_declared_direction_skips_check(self):
        snapshot = ConfirmSnapshot(self.BUY_BODY)
        self.assertTrue(ClientTrader._verify_snapshot_direction(snapshot, None))


class RepoSnapshotTests(unittest.TestCase):
    """回购页确认弹窗用「融资/融券」措辞，不能因此解析成空而误中止。"""

    def test_repo_labels_are_parsed(self):
        snapshot = ConfirmSnapshot(_clean(
            "证券代码：204001(GC001)\n"
            "融资价格：2.005\n"
            "融资数量：10\n"
        ))
        self.assertEqual(snapshot.price, "2.005")
        self.assertEqual(snapshot.amount, "10")

    def test_reverse_repo_labels_are_parsed(self):
        snapshot = ConfirmSnapshot(_clean(
            "证券代码：204001(GC001)\n"
            "融券价格：2.005\n"
            "融券数量：10\n"
        ))
        self.assertEqual(snapshot.price, "2.005")
        self.assertEqual(snapshot.amount, "10")


class MenuPathTests(unittest.TestCase):
    """回购菜单文案必须与客户端完全一致，否则会走模糊匹配挑错方向。"""

    def test_repo_paths_use_correct_character(self):
        from easytrader.config import client as client_config

        config = client_config.create("ths")
        self.assertEqual(config.REPO_MENU_PATH, ["债券回购", "融资回购（正回购）"])
        self.assertEqual(
            config.REVERSE_REPO_MENU_PATH, ["债券回购", "融券回购（逆回购）"]
        )

    def test_reverse_repo_path_has_no_wrong_variant_character(self):
        from easytrader.config import client as client_config

        config = client_config.create("ths")
        # 劵 是 券 的错字；写错后旧代码靠模糊匹配碰巧选中，极不可靠
        for label in config.REVERSE_REPO_MENU_PATH:
            self.assertNotIn("劵", label)


if __name__ == "__main__":
    unittest.main()
