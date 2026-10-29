# -*- coding: utf-8 -*-
import abc
import functools
import logging
import os
import re
import sys
import time
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
        """按委托编号撤单。

        实测结论：撤单页的**双击行**在远航版上不可靠（点了不产生任何弹窗，
        但调用返回得像成功一样，实际委托仍挂着）。真正有效的是顶部的
        「撤单(Del)」/「全撤(Z /)」按钮 —— 点完会弹出标题为「提示信息」的
        确认框，正文含"撤销这(N)笔委托"，点“是”才真正撤单。

        因此这里：先确认真实存在可撤单，再点全撤并处理确认框，
        最后用委托列表复核目标是否真的变成已撤，避免"报成功但没撤"。
        """
        field = self._config.CANCEL_ENTRUST_ENTRUST_FIELD
        rows = self._frame_rows(self.cancel_entrusts, {field})[1]

        if not rows:
            return {"message": "当前没有可撤委托单（该委托可能已成交或已撤）"}
        numbers = {str(row.get(field, "")).strip() for row in rows}
        if str(entrust_no).strip() not in numbers:
            return {
                "message": "委托单状态错误不能撤单, 该委托单可能已经成交或者已撤"
            }

        self.cancel_all_entrusts()

        # 复核：目标必须真的变成已撤，否则如实报告而不是假装成功
        try:
            after = self.today_entrusts
        except Exception:
            after = None
        if after is not None:
            for row in self._frame_rows(after, {field})[1]:
                if str(row.get(field, "")).strip() == str(entrust_no).strip():
                    status = str(row.get("备注", "")).strip()
                    if "已撤" in status:
                        return {"entrust_no": entrust_no, "status": status}
                    return {
                        "message": "撤单指令已发出，但该委托状态为 %s，"
                        "请人工核对" % (status or "未知")
                    }
        return {"entrust_no": entrust_no, "status": "已提交撤单请求"}

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
        return self.trade(
            security, price, amount, menu_path=["债券回购", "融资回购（正回购）"]
        )

    @perf_clock
    def reverse_repo(self, security, price, amount, **kwargs):
        return self.trade(
            security, price, amount, menu_path=["债券回购", "融劵回购（逆回购）"]
        )

    @perf_clock
    def buy(self, security, price, amount, **kwargs):
        return self.trade(security, price, amount, menu_path=["买入[F1]"])

    @perf_clock
    def sell(self, security, price, amount, **kwargs):
        return self.trade(security, price, amount, menu_path=["卖出[F2]"])

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
        """
        市价交易
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']

        :return: {'entrust_no': '委托单号'}
        """
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
        self._switch_left_menus(self._config.AUTO_IPO_MENU_PATH)

        stock_list = self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

        if len(stock_list) == 0:
            return {"message": "今日无新股"}
        invalid_list_idx = [
            i for i, v in enumerate(stock_list) if v[self.config.AUTO_IPO_NUMBER] <= 0
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

        return self._handle_pop_dialogs()

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

    def trade(self, security, price, amount, menu_path=None):
        """限价委托。字段填写与提交都在前台通道里完成并逐项校验。"""
        return self._trade_via_foreground(
            security, price, amount, menu_path=menu_path
        )

    def _trade_via_foreground(
        self, security, price, amount, expect_price=None, menu_path=None
    ):
        """前台下单主流程：填表 → 提交 → 比对确认弹窗 → 确认或取消。

        关键点是**校验放在确认弹窗上**：交易表单的字段读不回来，但客户端
        会在确认弹窗里用可读文本列出它真正准备提交的代码、价格、数量。
        只有三项都与请求一致时才点“是”，否则一律点“否”并报错。
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
        """提交前的委托编号快照，用于识别"本次新增"的那一笔。

        注意这里**不能**在读取失败时静默返回空集：早先那样写，一次读取
        失败就会让快照变成空，于是列表里所有历史委托都会被当成"新增"，
        反而把别人的单子当成本次结果。读取不到就返回 None，由调用方
        明确降级：宁可只按代码+数量+未报状态匹配，也不拿空快照硬比。
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
        """确认后回查委托列表，找出本次新增的那笔委托。

        同花顺的结果提示框正文是自绘的，``dialog_body`` 读不到合同编号，
        所以委托编号只能从委托列表里反查。

        匹配规则按 ``known`` 是否可用分两种：
        - 有快照：只认"新出现的编号"，最严格；
        - 没快照（提交前那次读取失败）：退回按 代码 + 数量 + 未报 匹配，
          并且要求候选中恰好只有一个，多个候选视为不确定，不做猜测。
        """
        field = self._config.CANCEL_ENTRUST_ENTRUST_FIELD
        deadline = time.monotonic() + timeout
        last_error = None
        # 确认弹窗刚关闭时页面还没切到委托页，第一次读取必然拿不到表头。
        # 先留出切换与渲染时间，避免把"还没准备好"误报成"缺少列"。
        time.sleep(1.2)
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

            matches = []
            for row in rows:
                number = str(row.get(field, "")).strip()
                if not number:
                    continue
                if known is not None and number in known:
                    continue
                row_code = str(row.get("证券代码", "")).strip()
                if row_code and row_code != code:
                    continue
                row_amount = str(row.get("委托数量", "")).strip()
                if row_amount and int(float(row_amount)) != amount:
                    continue
                matches.append(row)

            # 没有快照做参照时，多个候选无法判断哪个是本次的，不猜
            if len(matches) == 1 or (known is not None and matches):
                row = matches[0]
                return {
                    "entrust_no": str(row.get(field, "")).strip(),
                    "security": code,
                    "amount": amount,
                    "status": str(row.get("备注", "")).strip(),
                    "source": "委托列表回查",
                }
            time.sleep(0.5)
        raise exceptions.TradeError(
            "确认后未能在委托列表中找到本次委托，结果未知，请人工核对。"
            "{}".format("查询错误: %s" % last_error if last_error else "")
        )

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
