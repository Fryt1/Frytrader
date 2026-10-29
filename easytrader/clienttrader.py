# -*- coding: utf-8 -*-
import abc
import functools
import logging
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from typing import Type, Union

import hashlib, binascii

import easyutils
from pywinauto import findwindows, timings

from easytrader import (
    exceptions,
    grid_strategies,
    pop_dialog_handler,
    refresh_strategies,
)
from easytrader.config import client
from easytrader.grid_strategies import IGridStrategy
from easytrader.log import logger
from easytrader.refresh_strategies import IRefreshStrategy
from easytrader.utils.misc import file2dict
from easytrader.utils.perf import perf_clock

if not sys.platform.startswith("darwin"):
    import pywinauto
    import pywinauto.clipboard

class IClientTrader(abc.ABC):
    @property
    @abc.abstractmethod
    def app(self):
        """Return current app instance"""
        pass

    @property
    @abc.abstractmethod
    def main(self):
        """Return current main window instance"""
        pass

    @property
    @abc.abstractmethod
    def config(self):
        """Return current config instance"""
        pass

    @abc.abstractmethod
    def wait(self, seconds: float):
        """Wait for operation return"""
        pass

    @abc.abstractmethod
    def refresh(self):
        """Refresh data"""
        pass

    @abc.abstractmethod
    def is_exist_pop_dialog(self):
        pass


class ClientTrader(IClientTrader):
    _editor_need_type_keys = False
    # The strategy to use for getting grid data
    grid_strategy: Union[IGridStrategy, Type[IGridStrategy]] = grid_strategies.Copy
    _grid_strategy_instance: IGridStrategy = None
    refresh_strategy: IRefreshStrategy = refresh_strategies.Switch()

    def enable_type_keys_for_editor(self):
        """
        有些客户端无法通过 set_edit_text 方法输入内容，可以通过使用 type_keys 方法绕过
        """
        self._editor_need_type_keys = True

    @property
    def grid_strategy_instance(self):
        if self._grid_strategy_instance is None:
            self._grid_strategy_instance = (
                self.grid_strategy
                if isinstance(self.grid_strategy, IGridStrategy)
                else self.grid_strategy()
            )
            self._grid_strategy_instance.set_trader(self)
        return self._grid_strategy_instance

    def __init__(self):
        self._config = client.create(self.broker_type)
        self._app = None
        self._main = None
        self._toolbar = None
        self._grid_strategy_instance = None
        # 记录当前停留的菜单页，用于跳过重复切换
        self._current_menu_path = None

    @property
    def app(self):
        return self._app

    @property
    def main(self):
        return self._main

    @property
    def config(self):
        return self._config

    def connect(self, exe_path=None, **kwargs):
        """
        直接连接登陆后的客户端
        :param exe_path: 客户端路径类似 r'C:\\htzqzyb2\\xiadan.exe', 默认 r'C:\\htzqzyb2\\xiadan.exe'
        :return:
        """
        connect_path = exe_path or self._config.DEFAULT_EXE_PATH
        if connect_path is None:
            raise ValueError(
                "参数 exe_path 未设置，请设置客户端对应的 exe 地址,类似 C:\\客户端安装目录\\xiadan.exe"
            )

        self._app = pywinauto.Application().connect(path=connect_path, timeout=10)
        self._close_prompt_windows()
        self._main = self._app.top_window()
        self._init_toolbar()

    @property
    def broker_type(self):
        return "ths"

    @property
    def balance(self):
        self._switch_left_menus(["查询[F4]", "资金股票"])

        return self._get_balance_from_statics()

    def _init_toolbar(self):
        self._toolbar = self._main.child_window(class_name="ToolbarWindow32")

    def _get_balance_from_statics(self):
        result = {}
        for key, control_id in self._config.BALANCE_CONTROL_ID_GROUP.items():
            result[key] = float(
                self._main.child_window(
                    control_id=control_id, class_name="Static"
                ).window_text()
            )
        return result

    @property
    def position(self):
        self._switch_left_menus(["查询[F4]", "资金股票"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def today_entrusts(self):
        self._switch_left_menus(["查询[F4]", "当日委托"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def today_trades(self):
        self._switch_left_menus(["查询[F4]", "当日成交"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def cancel_entrusts(self):
        self.refresh()
        # 撤单页必须强制切一次：下单流程用前台通道直接切页，
        # 菜单缓存可能和实际页面不一致，跳过切页就会读到别的表。
        self._current_menu_path = None
        self._switch_left_menus(["撤单[F3]"])
        # 切页后表格要重新渲染才含当前挂单。实测立刻读取会拿到切页前的
        # 旧内容（表现为"没有可撤委托单"），所以要给渲染留出时间。
        self.wait(0.6)

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @perf_clock
    def cancel_entrust(self, entrust_no):
        """按委托编号撤指定的**一笔**委托。

        安全约束（重要）：这里**绝不允许**退化成“全撤”。早先的实现为了绕过
        “双击行不可靠”的问题，直接调用 ``cancel_all_entrusts()``，
        结果是“撤一笔”实际撤掉了账户里所有挂单——这是资金损失级的缺陷。
        离线回归见 tests/test_trade_result.py 的 CancelSingleOnlyTests。

        现在只有一条路径：撤单页上精确定位目标行 → 只对目标行发起撤单
        → 回查该行确实变成已撤。做不到精确定位时（找不到单笔撤单入口、
        表格里没有这一行、或该客户端的单笔撤单方式尚未离线验证）
        **在产生任何撤单副作用之前**抛 ``TradeError``，明确说明不支持。
        """
        field = self._config.CANCEL_ENTRUST_ENTRUST_FIELD
        wanted = str(entrust_no).strip()
        if not wanted:
            raise exceptions.TradeError("未提供委托编号，未执行撤单")

        rows = self._frame_rows(self.cancel_entrusts, {field})[1]
        if not rows:
            raise exceptions.TradeError(
                "当前没有可撤委托单（该委托可能已成交或已撤），未执行撤单"
            )

        target_index = None
        for index, row in enumerate(rows):
            if str(row.get(field, "")).strip() == wanted:
                target_index = index
                break
        if target_index is None:
            raise exceptions.TradeError(
                "委托单状态错误不能撤单, 该委托单可能已经成交或者已撤，未执行撤单"
            )

        pending_before = sum(1 for row in rows if self._is_pending_row(row))
        self._cancel_single_row(target_index, wanted)
        return {
            "entrust_no": wanted,
            "status": self._verify_single_cancelled(wanted, pending_before),
        }

    def _cancel_single_row(self, row_index, entrust_no):
        """只对撤单页的第 ``row_index`` 行发起撤单。

        撤单页上确实存在“撤单(Del)”（control_id 1099）按钮和行选择控件，
        但**针对单行的可靠操作路径尚未验证**：双击行实测不产生任何弹窗，
        而“全撤”一旦误触会撤掉全部挂单。在验证通过之前这里失败关闭，
        既不点“全撤”，也不“试一试”双击行。
        """
        raise exceptions.TradeError(
            "单笔撤单在当前客户端上尚未验证可安全执行（针对单行的可靠操作路径未确认；"
            "“全撤”会误撤其它委托，因此不使用）。为避免误撤，已拒绝执行；"
            "委托 %s 仍挂在账户里，请人工在客户端撤单。" % entrust_no
        )

    # 这些状态表示委托仍是**活动挂单**，不能当作撤单成功返回。
    _PENDING_ENTRUST_STATUS = ("未报", "已报", "部成", "部分成交", "待报", "正报")

    def _is_pending_row(self, row):
        """该行是否仍是活动挂单（未报/已报/部成等）。"""
        status = str(row.get("备注", "")).strip()
        if "已撤" in status:
            return False
        for bad in self._INVALID_ENTRUST_STATUS:
            if bad in status:
                return False
        return True

    def _verify_single_cancelled(self, entrust_no, expected_pending):
        """回查单笔撤单结果，返回该委托的最终状态。

        判定标准：
        - 目标必须是**已撤**（未报/已报等活动状态不算成功）；
        - 活动挂单数必须恰好减少 1。少得更多说明牵连了其它委托，
          必须立刻报错——这正是“撤一笔变全撤”的可观测特征。

        目前单笔撤单失败关闭，保留该实现是为了让后续验证有明确的完成判据。
        """
        field = self._config.CANCEL_ENTRUST_ENTRUST_FIELD
        after_rows = self._frame_rows(self.today_entrusts, {field})[1]
        after_pending = sum(1 for row in after_rows if self._is_pending_row(row))
        if after_pending < expected_pending - 1:
            raise exceptions.TradeError(
                "撤单后活动挂单数异常减少（%s → %s），可能牵连了其它委托，"
                "请立即人工核对" % (expected_pending, after_pending)
            )
        for row in after_rows:
            if str(row.get(field, "")).strip() != str(entrust_no).strip():
                continue
            status = str(row.get("备注", "")).strip()
            if "已撤" in status:
                return status
            for pending in self._PENDING_ENTRUST_STATUS:
                if pending in status:
                    raise exceptions.TradeError(
                        "撤单指令已发出，但委托 %s 仍为“%s”，请人工核对"
                        % (entrust_no, status)
                    )
            raise exceptions.TradeError(
                "撤单后委托 %s 状态为“%s”，无法确认已撤，请人工核对"
                % (entrust_no, status or "未知")
            )
        raise exceptions.TradeError("撤单后无法在委托列表中找到该委托，请人工核对")

    def cancel_all_entrusts(self):
        """全部撤单。

        撤单页顶部有一个"全撤(Z /)"按钮（control_id 30001）。实测点完弹出的
        确认框标题是 **「提示信息」**（不是「撤单确认」），正文形如
        「您确认要撤销这( 2 )笔委托吗？」；必须在这里点“是(Y)”才真正撤单，
        只关掉确认框等于取消操作。
        """
        self.refresh()
        self._current_menu_path = None
        self._switch_left_menus(["撤单[F3]"])
        self.wait(0.6)

        channel = self._trade_channel()
        channel.ensure_visible()
        channel.activate()

        button = channel.find(
            self._config.TRADE_CANCEL_ALL_ENTRUST_CONTROL_ID, "Button"
        )
        if not button:
            raise exceptions.TradeError("撤单页找不到“全撤”按钮，未执行撤单")
        channel.post_click(button)

        # 确认框标题是「提示信息」，正文含“撤销这”；按正文识别最稳。
        dialog = None
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            for hwnd in channel.dialogs():
                body = channel.dialog_body(hwnd)
                if "撤销" in body or "撤单" in channel.dialog_title(hwnd):
                    dialog = hwnd
                    break
            if dialog:
                break
            self.wait(0.2)

        if not dialog:
            # 没弹确认框说明没有可撤单，属于正常情况而不是失败
            return {"message": "没有弹出撤单确认，可能已无可撤委托"}

        channel.click_dialog(dialog, 6)  # 是(Y)
        self.wait(1.2)

        # 可能再跟一个结果提示，清掉避免挡住后续读取
        for leftover in channel.dialogs():
            channel._dismiss(leftover)
        return {"message": "已确认撤销全部委托"}

    @perf_clock
    def repo(self, security, price, amount, **kwargs):
        """正回购（融资）。页面：债券回购 / 融资回购（正回购）。"""
        return self.trade(
            security,
            price,
            amount,
            menu_path=self.config.REPO_MENU_PATH,
            direction="融资",
        )

    @perf_clock
    def reverse_repo(self, security, price, amount, **kwargs):
        """逆回购（融券）。页面：债券回购 / 融券回购（逆回购）。"""
        return self.trade(
            security,
            price,
            amount,
            menu_path=self.config.REVERSE_REPO_MENU_PATH,
            direction="融券",
        )

    @perf_clock
    def buy(self, security, price, amount, **kwargs):
        return self.trade(
            security, price, amount, menu_path=["买入[F1]"], direction="买入"
        )

    @perf_clock
    def sell(self, security, price, amount, **kwargs):
        return self.trade(
            security, price, amount, menu_path=["卖出[F2]"], direction="卖出"
        )

    @perf_clock
    def market_buy(self, security, amount, ttype=None, limit_price=None, **kwargs):
        """
        市价买入
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']
        :param limit_price: 科创板 限价

        :return: {'entrust_no': '委托单号'}
        """
        self._switch_left_menus(["市价委托", "买入"])

        return self.market_trade(security, amount, ttype, limit_price=limit_price)

    @perf_clock
    def market_sell(self, security, amount, ttype=None, limit_price=None, **kwargs):
        """
        市价卖出
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']
        :param limit_price: 科创板 限价
        :return: {'entrust_no': '委托单号'}
        """
        self._switch_left_menus(["市价委托", "卖出"])

        return self.market_trade(security, amount, ttype, limit_price=limit_price)

    def market_trade(self, security, amount, ttype=None, limit_price=None, **kwargs):
        """市价交易。

        **同花顺(ths)当前不支持下单**（失败关闭）：市价委托没有可比对的限价
        字段，而本客户端的确认弹窗正文与结果提示框都不可靠解析
        （见 foreground_trade 模块头），``TradePopDialogHandler`` 又只会
        盲目按“是(Y)”提交。在没有离线可验证的比对方式前，ths 在**填写任何
        字段、提交任何消息之前**就抛错，绝不产生“看似成功”的假象。

        其他券商适配器保持原有实现不变（它们的确认弹窗可解析）。
        """
        if self.broker_type == "ths":
            raise exceptions.TradeError(
                "市价委托（market_buy/market_sell）在同花顺客户端上尚无"
                "可离线验证的确认比对方式，为避免无校验提交，已拒绝执行。"
                "请改用 buy/sell 限价委托。"
            )
        return self._market_trade_original(
            security, amount, ttype, limit_price=limit_price
        )

    def _market_trade_original(self, security, amount, ttype=None, limit_price=None):
        """其他券商适配器的原有市价下单流程。"""
        code = security[-6:]
        self._type_edit_control_keys(self._config.TRADE_SECURITY_CONTROL_ID, code)
        if ttype is not None:
            retry = 0
            retry_max = 10
            while retry < retry_max:
                try:
                    self._set_market_trade_type(ttype)
                    break
                except:
                    retry += 1
                    self.wait(0.1)
            else:
                # 市价类型没选上时绝不能继续提交，
                # 否则会按客户端残留的旧选项下单，成交价与预期完全不同。
                raise exceptions.TradeError(
                    "无法选择市价委托类型: {}，已中止下单".format(ttype)
                )
        self._set_market_trade_params(security, amount, limit_price=limit_price)
        self._submit_trade()

        return self._require_entrust_result(
            self._handle_pop_dialogs(
                handler_class=pop_dialog_handler.TradePopDialogHandler
            )
        )

    def _set_market_trade_type(self, ttype):
        """根据选择的市价交易类型选择对应的下拉选项"""
        selects = self._main.child_window(
            control_id=self._config.TRADE_MARKET_TYPE_CONTROL_ID, class_name="ComboBox"
        )
        for i, text in enumerate(selects.texts()):
            # skip 0 index, because 0 index is current select index
            if i == 0:
                if re.search(ttype, text):  # 当前已经选中
                    return
                else:
                    continue
            if re.search(ttype, text):
                selects.select(i - 1)
                return
        raise TypeError("不支持对应的市价类型: {}".format(ttype))

    def _set_stock_exchange_type(self, ttype):
        """根据选择的市价交易类型选择对应的下拉选项"""
        selects = self._main.child_window(
            control_id=self._config.TRADE_STOCK_EXCHANGE_CONTROL_ID, class_name="ComboBox"
        )

        for i, text in enumerate(selects.texts()):
            # skip 0 index, because 0 index is current select index
            if i == 0:
                if ttype.strip() == text.strip():  # 当前已经选中
                    return
                else:
                    continue
            if ttype.strip() == text.strip():
                selects.select(i - 1)
                return
        raise TypeError("不支持对应的市场类型: {}".format(ttype))

    def auto_ipo(self):
        """批量新股申购。

        返回**确定**结果：要么明确说“没有可申购的新股”，要么返回
        ``{"message": ..., "result": "unknown"}`` / 抛错，绝不把
        “什么都没发生”当作成功返回 None。
        """
        self._switch_left_menus(self._config.AUTO_IPO_MENU_PATH)

        stock_list = self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

        if len(stock_list) == 0:
            return {"message": "今日无新股"}

        invalid_list_idx = [
            i
            for i, v in enumerate(stock_list)
            if self._ipo_quantity(v) <= 0
        ]

        if len(stock_list) == len(invalid_list_idx):
            return {"message": "没有发现可以申购的新股"}

        self._click(self._config.AUTO_IPO_SELECT_ALL_BUTTON_CONTROL_ID)
        self.wait(0.1)

        for row in invalid_list_idx:
            self._click_grid_by_row(row)
        self.wait(0.1)

        self._click(self._config.AUTO_IPO_BUTTON_CONTROL_ID)
        self.wait(0.1)

        result = self._handle_pop_dialogs()
        if isinstance(result, dict):
            return result
        # 没弹窗/弹窗未收敛时，代码根本不知道有没有申购成功。
        # 早先直接返回 None，调用方会误当成成功。
        return {
            "message": "申购指令已发出，但客户端未给出可读结果，请人工核对",
            "result": "unknown",
        }

    @staticmethod
    def _ipo_quantity(row):
        """读取一行里的可申购数量。

        表格经 ``na_filter=False`` 解析，空单元格是空字符串而不是 0，
        早先直接 ``v[申购数量] <= 0`` 会在空串上抛 TypeError；
        这里统一转成数字，无法解析的按 0（不可申购）处理。
        """
        raw = row.get("申购数量", row.get("可申购数量", 0))
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return raw
        text = str(raw).strip().replace(",", "")
        if not text:
            return 0
        try:
            return float(text)
        except ValueError:
            return 0

    def _click_grid_by_row(self, row):
        x = self._config.COMMON_GRID_LEFT_MARGIN
        y = (
            self._config.COMMON_GRID_FIRST_ROW_HEIGHT
            + self._config.COMMON_GRID_ROW_HEIGHT * row
        )
        self._app.top_window().child_window(
            control_id=self._config.COMMON_GRID_CONTROL_ID,
            class_name="CVirtualGridCtrl",
        ).click(coords=(x, y))

    @perf_clock
    def is_exist_pop_dialog(self):
        self.wait(0.5)  # wait dialog display
        try:
            return (
                self._main.wrapper_object() != self._app.top_window().wrapper_object()
            )
        except (
            findwindows.ElementNotFoundError,
            timings.TimeoutError,
            RuntimeError,
        ) as ex:
            logger.exception("check pop dialog timeout")
            return False

    @perf_clock
    def close_pop_dialog(self):
        try:
            if self._main.wrapper_object() != self._app.top_window().wrapper_object():
                w = self._app.top_window()
                if w is not None:
                    w.close()
                    self.wait(0.2)
        except (
                findwindows.ElementNotFoundError,
                timings.TimeoutError,
                RuntimeError,
        ) as ex:
            pass

    def _run_exe_path(self, exe_path):
        return os.path.join(os.path.dirname(exe_path), "xiadan.exe")

    def wait(self, seconds):
        time.sleep(seconds)

    def exit(self):
        self._app.kill()

    def _close_prompt_windows(self):
        self.wait(1)
        for window in self._app.windows(class_name="#32770", visible_only=True):
            title = window.window_text()
            if title != self._config.TITLE:
                logging.info("close window %s" % title)
                window.close()
                self.wait(0.2)
        self.wait(1)

    def close_pormpt_window_no_wait(self):
        for window in self._app.windows(class_name="#32770"):
            if window.window_text() != self._config.TITLE:
                window.close()

    def trade(self, security, price, amount, menu_path=None, direction=None):
        """限价委托。字段填写与提交都在前台通道里完成并逐项校验。"""
        return self._trade_via_foreground(
            security, price, amount, menu_path=menu_path, direction=direction
        )

    def _trade_via_foreground(
        self,
        security,
        price,
        amount,
        expect_price=None,
        menu_path=None,
        direction=None,
    ):
        """前台下单主流程：填表 → 提交 → 比对确认弹窗 → 确认或取消。

        关键点是**校验放在确认弹窗上**：交易表单的字段读不回来，但客户端
        会在确认弹窗里用可读文本列出它真正准备提交的代码、价格、数量。
        只有三项都与请求一致时才点“是”，否则一律点“否”并报错。

        ``direction`` 是调用方声明的方向（买入/卖出/融资/融券）。只有确认
        弹窗里也能读出方向时才比对；读不出就跳过，绝不因为“当前在哪个页面”
        就假定方向正确。
        """
        channel = self._trade_channel()
        channel.ensure_visible()
        if not channel.activate():
            raise exceptions.TradeError(
                "无法把下单窗口切到前台，键入会落到别的程序上，已中止"
            )

        code = str(security)[-6:]

        # 必须先切到交易页再取"已有委托快照"：确认弹窗开着的时候去切页
        # 会把本次委托连同弹窗一起弄丢；而快照又必须在提交前拿到。
        if menu_path:
            channel.select_page(menu_path)
            # 前台通道是绕过菜单缓存直接切页的，这里必须把缓存作废，
            # 否则后续 today_entrusts 会以为还在原页面而跳过切页，
            # 读到的是买入页的表格——表现就是"缺少合同编号列"。
            self._current_menu_path = None
        known = self._entrust_numbers()
        if known is None:
            # 没有完整基线就无法判定哪一笔是本次新增的。
            # 必须在填表/提交**之前**中止：宁可不下单，也不能靠猜测把
            # 别人的委托当成本次结果（上层会拿错编号去撤单）。
            raise exceptions.TradeError(
                "提交前无法读取完整委托列表作为基线，无法确认本次委托结果，"
                "已在下单前中止（未产生任何委托）"
            )
        if menu_path:
            channel.select_page(menu_path)
            self._current_menu_path = None

        self._set_trade_params(security, price, amount)

        # 代码是否真被客户端接受：证券简称由客户端自己查出来
        name = channel.wait_stock_name()
        logger.debug("证券代码 %s 已被客户端接受，简称 %s", code, name)

        # 前台激活+键入会触发客户端的验证码拦截。验证码必须在下单前解决，
        # 否则提交后弹出来的是验证码框而不是委托确认。
        self._solve_captcha_if_any()

        dialog, snapshot = channel.submit()
        wanted_price = (
            expect_price
            if expect_price is not None
            else easyutils.round_price_by_code(price, code)
        )
        try:
            self._verify_snapshot(snapshot, code, wanted_price, int(amount))
            self._verify_snapshot_direction(snapshot, direction)
        except exceptions.TradeError:
            channel.reject(dialog)
            raise

        result = channel.confirm(dialog)
        if result.get("entrust_no"):
            return result
        # 结果提示框的正文是自绘的，读不到编号；改为回查委托列表。
        # 回查前必须做两件事：
        # 1. 清掉残留提示框，否则它会挡住表格；
        # 2. 作废菜单缓存——前台通道是直接 TVM_SELECTITEM 切页的，
        #    缓存还停在交易页，会让 today_entrusts 跳过切页读到错的表。
        channel.dismiss_leftovers()
        self._current_menu_path = None
        return self._await_new_entrust(code, int(amount), known)

    @staticmethod
    def _verify_snapshot_direction(snapshot, direction):
        """确认弹窗必须能证明方向，且与调用方声明的方向一致。

        调用方声明了方向（buy/sell/repo/reverse_repo）时，弹窗里**读不到**
        方向同样视为校验失败：无法证明的委托不允许提交，不能靠“当前在哪个
        页面”去推断。只有调用方不声明方向时才跳过。
        """
        if not direction:
            return True
        actual = getattr(snapshot, "direction", None)
        if actual is None:
            raise exceptions.TradeError(
                "确认弹窗未能读出委托方向，无法确认是%s，已取消委托"
                % direction
            )
        if str(actual).strip() != str(direction).strip():
            raise exceptions.TradeError(
                "确认弹窗方向与请求不一致，已取消委托：期望 %s 实际 %s"
                % (direction, actual)
            )
        return True

    def _solve_captcha_if_any(self):
        """前台交易期间若弹出验证码，先用既有识别流程通过它。

        远航版在交易窗口被激活并接受键盘输入后会弹验证码，这是它的
        防自动化措施。验证码不解决，点“买入”后弹出来的是验证码框，
        委托根本发不出去。识别失败时明确抛错，绝不带着未通过的验证码
        继续提交。
        """
        strategy = self.grid_strategy_instance
        if not hasattr(strategy, "_get_captcha_dialog"):
            return False
        try:
            dialog = strategy._get_captcha_dialog()
        except Exception:
            return False
        if dialog is None:
            return False
        logger.info("检测到验证码弹窗，正在处理")
        if not strategy._solve_captcha():
            raise exceptions.TradeError(
                "验证码未通过，已中止委托（未提交任何委托）"
            )
        return True

    @staticmethod
    def _frame_rows(frame, required=None):
        """把表格结果统一成 (列名集合, 行字典列表)。

        ``today_entrusts`` 等接口在本机实测返回的是 ``list[dict]``
        而不是 DataFrame，早先按 DataFrame 写的判断会直接抛
        ``AttributeError: 'list' object has no attribute 'columns'``。
        """
        if frame is None:
            return set(), []
        if hasattr(frame, "to_dict") and hasattr(frame, "columns"):
            columns = {str(c) for c in frame.columns}
            if required and not required.issubset(columns):
                return columns, []
            return columns, frame.to_dict("records")
        if isinstance(frame, list):
            columns = set()
            rows = []
            for item in frame:
                if isinstance(item, dict):
                    columns.update(str(k) for k in item)
                    rows.append(item)
            if required and not required.issubset(columns):
                return columns, []
            return columns, rows
        return set(), []

    def _entrust_numbers(self):
        """提交前的委托编号基线，用于识别"本次新增"的那一笔。

        这里**不能**在读取失败时静默返回空集：早先那样写，一次读取
        失败就会让基线变成空，于是列表里所有历史委托都会被当成"新增"，
        反而把别人的单子当成本次结果。读不到就返回 ``None``，
        调用方 ``_trade_via_foreground`` 会据此在提交前直接中止。
        """
        field = self._config.CANCEL_ENTRUST_ENTRUST_FIELD
        try:
            frame = self.today_entrusts
        except Exception:
            return None
        columns, rows = self._frame_rows(frame, {field})
        if field not in columns:
            return None
        return {
            str(row.get(field, "")).strip()
            for row in rows
            if str(row.get(field, "")).strip()
        }

    def _await_new_entrust(self, code, amount, known, timeout=25.0):
        """确认后回查委托列表，找出本次新增的那一笔委托。

        同花顺的结果提示框正文是自绘的，``dialog_body`` 读不到合同编号，
        所以委托编号只能从委托列表里反查。

        ``known`` 必须是提交前的**完整**基线。``None`` 表示读不到基线，
        此时无法判定哪一笔是本次新增的，直接报错——即使本方法被私有直接调用
        也不会退化成“按代码+数量猜”。判定规则只有一个：候选必须满足
        “编号不在基线里 + 代码相同 + 数量精确相同 + 状态不是废单/失败/已撤”，
        且**恰好只有一个**。多于一个候选就继续等，等不到就报结果未知——
        绝不返回 ``matches[0]`` 去赌哪一笔是本次的。
        """
        if known is None:
            raise exceptions.TradeError(
                "缺少提交前的委托编号基线，无法唯一确定本次委托，结果未知，"
                "请人工核对委托列表"
            )
        field = self._config.CANCEL_ENTRUST_ENTRUST_FIELD
        deadline = time.monotonic() + timeout
        last_error = "基线与候选均不满足唯一匹配"
        last_candidates = []
        # 确认弹窗刚关闭时页面还没切到委托页，第一次读取必然拿不到表头。
        # 先留出切换与渲染时间，避免把"还没准备好"误报成"缺少列"。
        # 走 self.wait 而不是 time.sleep：子类/测试可以覆盖这段等待。
        self.wait(1.2)
        while time.monotonic() < deadline:
            try:
                self._solve_captcha_if_any()
                frame = self.today_entrusts
            except exceptions.TradeError:
                raise
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
                continue
            columns, rows = self._frame_rows(frame, {field})
            if field not in columns:
                last_error = "委托列表缺少 %s 列（当前列：%s）" % (
                    field,
                    ",".join(sorted(columns)) or "无",
                )
                time.sleep(0.5)
                continue

            matches = [
                row
                for row in rows
                if self._is_new_entrust_row(row, code, amount, known, field)
            ]
            last_candidates = [
                str(row.get(field, "")).strip() for row in matches
            ]
            if len(matches) == 1:
                row = matches[0]
                return {
                    "entrust_no": str(row.get(field, "")).strip(),
                    "security": code,
                    "amount": amount,
                    "status": str(row.get("备注", "")).strip(),
                    "source": "委托列表回查",
                }
            if len(matches) > 1:
                last_error = "出现多笔疑似本次委托，无法唯一确定"
            time.sleep(0.5)
        raise exceptions.TradeError(
            "确认后未能在委托列表中找到唯一的本次委托，结果未知，请人工核对。"
            "候选=%s；%s"
            % (last_candidates or "无", "查询错误: %s" % last_error)
        )

    def _is_new_entrust_row(self, row, code, amount, known, field):
        """判断某行是否是本次新增且仍然有效的委托。

        逐项硬条件，任何一项不满足都排除：
        - 编号非空且不在基线里；
        - 证券代码一致（行里没有该列时视为不满足，不做“空即匹配”的放宽）；
        - 委托数量一致；
        - 状态不是废单/失败/拒绝——这些行虽然“新出现”，但不是有效委托，
          拿它的编号交给撤单只会得到一个假结果。
        """
        number = str(row.get(field, "")).strip()
        if not number:
            return False
        if known is not None and number in known:
            return False

        row_code = str(row.get("证券代码", "")).strip()
        if row_code != code:
            return False

        row_amount = str(row.get("委托数量", "")).strip()
        if not row_amount:
            return False
        try:
            # 必须**精确**相等：早先用 int(float(...)) 比较，100.9 会被截断成 100，
            # 把数量不同的委托误认成本次的那一笔。这里按十进制比较。
            if Decimal(row_amount.replace(",", "")) != Decimal(int(amount)):
                return False
        except (TypeError, ValueError, InvalidOperation):
            return False

        status = str(row.get("备注", "")).strip()
        for bad in self._INVALID_ENTRUST_STATUS:
            if bad in status:
                return False
        return True

    # 这些状态说明委托没有被券商受理，不能当作“本次成功的委托”。
    _INVALID_ENTRUST_STATUS = ("废单", "失败", "拒绝", "已撤", "撤单")

    @staticmethod
    def _verify_snapshot(snapshot, code, price, amount):
        """确认弹窗里的内容必须与请求完全一致，否则不许提交。"""
        problems = []
        if snapshot.security != code:
            problems.append("证券代码 期望 %s 实际 %s" % (code, snapshot.security))
        if snapshot.amount is None or int(snapshot.amount) != int(amount):
            problems.append("委托数量 期望 %s 实际 %s" % (amount, snapshot.amount))
        try:
            actual_price = float(snapshot.price) if snapshot.price else None
        except (TypeError, ValueError):
            actual_price = None
        if actual_price is None or abs(actual_price - float(price)) > 1e-6:
            problems.append("委托价格 期望 %s 实际 %s" % (price, snapshot.price))
        if problems:
            raise exceptions.TradeError(
                "确认弹窗内容与请求不一致，已取消委托：%s" % "；".join(problems)
            )
        return True

    @staticmethod
    def _require_entrust_result(result):
        """下单必须以拿到委托编号为准。

        客户端弹窗关闭并不代表券商已受理，没有委托编号时一律视为结果未知，
        抛出异常交给人工核对，绝不能返回一个看起来成功的空结果。
        """
        if isinstance(result, dict) and result.get("entrust_no"):
            return result
        raise exceptions.TradeError(
            "未获取到委托编号，委托结果未知，请人工核对委托列表。"
            "客户端返回: {}".format(result)
        )

    def _click(self, control_id):
        self._app.top_window().child_window(
            control_id=control_id, class_name="Button"
        ).click()

    @perf_clock
    def _submit_trade(self):
        time.sleep(0.2)
        self._main.child_window(
            control_id=self._config.TRADE_SUBMIT_CONTROL_ID, class_name="Button"
        ).click()

    @perf_clock
    def __get_top_window_pop_dialog(self):
        return self._app.top_window().window(
            control_id=self._config.POP_DIALOD_TITLE_CONTROL_ID
        )

    @perf_clock
    def _get_pop_dialog_title(self):
        return (
            self._app.top_window()
            .child_window(control_id=self._config.POP_DIALOD_TITLE_CONTROL_ID)
            .window_text()
        )

    def _set_trade_params(self, security, price, amount):
        code = str(security)[-6:]

        # 先清成空表单，避免上一笔的残留值被带进来
        self._trade_channel().refill()

        self._type_edit_control_keys(self._config.TRADE_SECURITY_CONTROL_ID, code)

        # 设置交易所
        if str(security).lower().startswith("sz"):
            self._set_stock_exchange_type("深圳Ａ股")
        if str(security).lower().startswith("sh"):
            self._set_stock_exchange_type("上海Ａ股")

        self._type_edit_control_keys(
            self._config.TRADE_PRICE_CONTROL_ID,
            easyutils.round_price_by_code(price, code),
        )
        self._type_edit_control_keys(
            self._config.TRADE_AMOUNT_CONTROL_ID, str(int(amount))
        )

    def _set_market_trade_params(self, security, amount, limit_price=None):
        self._type_edit_control_keys(
            self._config.TRADE_AMOUNT_CONTROL_ID, str(int(amount))
        )
        self.wait(0.1)
        price_control = None
        if str(security).startswith("68"):  # 科创板存在限价
            try:
                price_control = self._main.child_window(
                    control_id=self._config.TRADE_PRICE_CONTROL_ID, class_name="Edit"
                )
            except:
                pass
        if price_control is not None:
            price_control.set_edit_text(limit_price)

    def _get_grid_data(self, control_id):
        return self.grid_strategy_instance.get(control_id)

    def _type_keys(self, control_id, text):
        self._main.child_window(control_id=control_id, class_name="Edit").set_edit_text(
            text
        )

    def _type_edit_control_keys(self, control_id, text):
        """写入交易字段。

        远航版的交易表单只认真实键盘输入：``set_edit_text``（底层
        ``WM_SETTEXT``）会被静默忽略；而三个 Edit 的文本**读不回来**
        （``WM_GETTEXT`` / ``EM_GETLINE`` / UIA ValuePattern 全部返回空，
        文本由客户端自绘）。所以这里既不能用 ``set_edit_text``，也不能做
        写入后回读比对——真正的校验落在下单确认弹窗上，见
        ``_trade_via_foreground``。
        """
        self._trade_channel().type_field(control_id, text)

    def _trade_channel(self):
        """惰性建立前台交易通道。"""
        if getattr(self, "_channel", None) is None:
            from easytrader.foreground_trade import ForegroundTradeChannel

            self._channel = ForegroundTradeChannel(
                self._main.wrapper_object().handle,
                tree_hwnd=self._find_tree_hwnd(),
            )
        return self._channel

    def _find_tree_hwnd(self):
        try:
            return int(self._get_left_menus_handle().wrapper_object().handle)
        except Exception:
            return None

    def type_edit_control_keys(self, editor, text):
        if not self._editor_need_type_keys:
            editor.set_edit_text(text)
        else:
            editor.select()
            editor.type_keys(text)

    def _collapse_left_menus(self):
        items = self._get_left_menus_handle().roots()
        for item in items:
            item.collapse()

    @perf_clock
    def _switch_left_menus(self, path, sleep=0.05):
        """切换到左侧菜单对应的页面。

        两项优化（实测有效）：
        1. 记录当前页面，同一页面不重复切换，连续查询同一页时省掉整次切换；
        2. 不再额外发 F5。实测切页后表格数据已是新的，F5 属于多余的一次
           真实按键——既拖慢速度，又会让窗口抢前台。
        """
        target = tuple(path)
        if getattr(self, "_current_menu_path", None) == target:
            return
        self.close_pop_dialog()
        self._get_left_menus_handle().get_item(path).select()
        self._current_menu_path = target
        self.wait(sleep)

    def _switch_left_menus_by_shortcut(self, shortcut, sleep=0.5):
        # 快捷键会改变当前页面，缓存失效
        self._current_menu_path = None
        self.close_pop_dialog()
        self._app.top_window().type_keys(shortcut)
        self.wait(sleep)

    @functools.lru_cache()
    def _get_left_menus_handle(self):
        count = 2
        while True:
            try:
                handle = self._main.child_window(
                    control_id=129, class_name="SysTreeView32"
                )
                if count <= 0:
                    return handle
                # sometime can't find handle ready, must retry
                handle.wait("ready", 2)
                return handle
            # pylint: disable=broad-except
            except Exception as ex:
                logger.exception("error occurred when trying to get left menus")
            count = count - 1

    def _cancel_entrust_by_double_click(self, row):
        x = self._config.CANCEL_ENTRUST_GRID_LEFT_MARGIN
        y = (
            self._config.CANCEL_ENTRUST_GRID_FIRST_ROW_HEIGHT
            + self._config.CANCEL_ENTRUST_GRID_ROW_HEIGHT * row
        )
        self._app.top_window().child_window(
            control_id=self._config.COMMON_GRID_CONTROL_ID,
            class_name="CVirtualGridCtrl",
        ).double_click(coords=(x, y))

    def refresh(self):
        self.refresh_strategy.set_trader(self)
        self.refresh_strategy.refresh()

    @perf_clock
    def _handle_pop_dialogs(self, handler_class=pop_dialog_handler.PopDialogHandler):
        """处理交易后的弹窗，返回结果。

        必须在有限次数内结束并给出确定结论：早先的实现里，
        遇到无法识别的弹窗时既不关闭也不返回，会在 while 循环里一直卡住。
        """
        handler = handler_class(self._app)
        max_rounds = 10

        for _ in range(max_rounds):
            if not self.is_exist_pop_dialog():
                # 弹窗已消失，但这里不能断言委托成功，调用方需核对委托列表
                return None

            try:
                title = self._get_pop_dialog_title()
            except pywinauto.findwindows.ElementNotFoundError:
                return None

            result = handler.handle(title)
            if result:
                return result

        # 连续处理仍未收敛：关闭残留弹窗并明确报错，不要静默当作成功
        logger.error("弹窗处理超过 %s 轮仍未结束，可能存在未知提示框", max_rounds)
        self.close_pop_dialog()
        raise exceptions.TradeError(
            "交易弹窗无法确认，委托结果未知，请人工核对委托列表"
        )


class BaseLoginClientTrader(ClientTrader):
    @abc.abstractmethod
    def login(self, user, password, exe_path, comm_password=None, **kwargs):
        """Login Client Trader"""
        pass

    def prepare(
        self,
        config_path=None,
        user=None,
        password=None,
        exe_path=None,
        comm_password=None,
        captcha=None,
        **kwargs
    ):
        """
        登陆客户端
        :param config_path: 登陆配置文件，跟参数登陆方式二选一
        :param user: 账号
        :param password: 明文密码
        :param exe_path: 客户端路径类似 r'C:\\htzqzyb2\\xiadan.exe', 默认 r'C:\\htzqzyb2\\xiadan.exe'
        :param comm_password: 通讯密码
        :param captcha: 验证码识别配置
        :return:
        """
        if config_path is not None:
            account = file2dict(config_path)
            user = account["user"]
            password = account["password"]
            comm_password = account.get("comm_password")
            exe_path = account.get("exe_path")
            captcha = account.get("captcha", captcha)
        self.login(
            user,
            password,
            exe_path or self._config.DEFAULT_EXE_PATH,
            comm_password,
            **kwargs
        )
        self._init_toolbar()
        if captcha:
            strategy = self.grid_strategy_instance
            configure = getattr(strategy, "configure_captcha", None)
            if configure is not None:
                configure(captcha)
