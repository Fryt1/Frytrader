# -*- coding: utf-8 -*-
"""同花顺远航版下单窗口的前台交易通道。

为什么需要这一层
================
远航版（xiadan.exe）的交易表单有两条实测出来的硬限制，直接沿用
pywinauto 的常规写法会出错：

1. **只认真实键盘输入。**
   ``WM_SETTEXT`` / ``EM_REPLACESEL`` / ``WM_CHAR`` 这类跨进程消息会被
   客户端静默忽略：调用返回成功，但字段里没有内容。只有真正把窗口放到
   前台、让控件获得焦点再逐字键入才写得进去。

2. **字段文本不可读。**
   ``GetWindowText`` / ``WM_GETTEXT`` / ``EM_GETLINE`` / UIA
   ``ValuePattern`` 对代码、价格、数量三个 Edit 全部返回空字符串，
   文本由客户端自绘。**任何"写入后回读比对"的校验在同花顺上都会误判。**

因此本模块不用读回校验，改用客户端自己产生的、可读的输出作为凭证：

- 证券名称 ``Static`` 控件（control_id 1036）：代码一旦被客户端接受，
  客户端会自行查询并显示证券简称，清空后重新变空。它证明"代码确实进去了"。
- 下单确认弹窗 ``#32770``（标题控件 1365 = ``委托确认``）的正文
  ``Static``（control_id 1040）：里面是客户端自己渲染的证券代码、价格、
  数量、预估金额。**提交前逐项比对这张单子，不一致就取消。**

模块只做 UI 通道，不决定下不下单；下单策略在 ``clienttrader`` 里。

安全约束
========
- 不点"我确认"之外的任何提交路径；确认前必须完成比对。
- 比对不通过一律按"否"取消，并抛 ``TradeError``。
- 任何一步超时都不静默继续，抛异常交给上层。
"""

import html
import re
import time

from easytrader import exceptions
from easytrader.log import logger

__all__ = ["ForegroundTradeChannel", "ConfirmSnapshot"]

# 控件 id（同花顺远航版实测）
CONTROL_SECURITY = 1032
CONTROL_PRICE = 1033
CONTROL_AMOUNT = 1034
CONTROL_SUBMIT = 1006
CONTROL_REFILL = 1007
CONTROL_STOCK_NAME = 1036

DIALOG_CLASS = "#32770"
DIALOG_TITLE_CONTROL = 1365
DIALOG_BODY_CONTROL = 1040
DIALOG_YES = 6
DIALOG_NO = 7
DIALOG_OK = 2

BM_CLICK = 0x00F5
WM_CLOSE = 0x0010
TVM_SELECTITEM = 0x110B
TVGN_CARET = 0x0009

_TAG = re.compile(r"<[^>]*>")


def _clean(text):
    """去掉客户端在正文里塞的 HTML 片段，返回纯文本。"""
    return html.unescape(_TAG.sub("", text or "")).strip()


class ConfirmSnapshot:
    """下单确认弹窗里的字段，全部来自客户端自己的渲染。"""

    __slots__ = ("raw", "account", "security", "name", "price", "amount", "total")

    def __init__(self, raw):
        self.raw = raw
        self.account = self._field(r"资金帐号[:：]\s*(\S+)")
        match = re.search(r"证券代码[:：]\s*(\d{6})\s*[（(]?([^）)\n]*)", raw)
        self.security = match.group(1) if match else None
        self.name = (match.group(2) or "").strip() if match else None
        self.price = self._number(r"(?:买入|卖出|委托)价格[:：]\s*([\d.]+)")
        self.amount = self._number(r"(?:买入|卖出|委托)数量[:：]\s*([\d]+)")
        self.total = self._number(r"预估金额[:：]\s*([\d.]+)")

    def _field(self, pattern):
        match = re.search(pattern, self.raw)
        return match.group(1).strip() if match else None

    def _number(self, pattern):
        match = re.search(pattern, self.raw)
        return match.group(1) if match else None

    def as_dict(self):
        return {
            "account": self.account,
            "security": self.security,
            "name": self.name,
            "price": self.price,
            "amount": self.amount,
            "total": self.total,
        }

    def __repr__(self):
        return "ConfirmSnapshot(%r)" % (self.as_dict(),)


class ForegroundTradeChannel:
    """把下单窗口临时放到前台，用真实键盘完成一次委托。

    调用方负责生命周期；本类不缓存跨次状态，每次都按句柄重新定位控件，
    因为客户端切页会重建控件。
    """

    def __init__(self, root_hwnd, tree_hwnd=None, native=None, owner_hwnd=None):
        self.root = int(root_hwnd)
        self.tree = int(tree_hwnd) if tree_hwnd else None
        # 复用后台模式已经很薄的 Win32 封装；没有就自己建一个。
        if native is None:
            from easytrader.background_windows import _Native

            native = _Native()
        self._n = native
        self.owner = int(owner_hwnd) if owner_hwnd else None
        self._pid = self._n.process_id(self.root)

    # ------------------------------------------------------------------
    # 窗口状态
    # ------------------------------------------------------------------
    @property
    def pid(self):
        return self._pid

    def ensure_visible(self):
        """显示下单窗口但不激活，供后续键入使用。"""
        if not self._n.is_window(self.root):
            raise exceptions.TradeError("下单窗口已关闭，请重新连接客户端")
        self._n.set_visible_no_activate(self.root)

    def activate(self):
        """把下单窗口变成前台窗口。

        没有这一步，键入的字符会进到用户当前正在用的窗口里。
        返回 True 表示前台已经属于下单进程。
        """
        import win32api
        import win32gui
        import win32process

        if self._foreground_pid() == self._pid:
            return True
        foreground = win32gui.GetForegroundWindow()
        foreground_thread = (
            win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        )
        my_thread = win32api.GetCurrentThreadId()
        attached = False
        try:
            if foreground_thread and foreground_thread != my_thread:
                win32process.AttachThreadInput(foreground_thread, my_thread, True)
                attached = True
            # 模拟一次 ALT，绕开系统的前台锁定
            win32api.keybd_event(0x12, 0, 0, 0)
            win32api.keybd_event(0x12, 0, 2, 0)
            win32gui.BringWindowToTop(self.root)
            win32gui.SetForegroundWindow(self.root)
        except Exception as exc:  # pragma: no cover - 依赖当前桌面状态
            logger.warning("激活下单窗口失败: %s", exc)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(foreground_thread, my_thread, False)
                except Exception:
                    pass
        return self._foreground_pid() == self._pid

    def _foreground_pid(self):
        import win32gui
        import win32process

        foreground = win32gui.GetForegroundWindow()
        if not foreground:
            return 0
        try:
            return int(win32process.GetWindowThreadProcessId(foreground)[1])
        except Exception:
            return 0

    def assert_foreground(self):
        """确认前台属于下单进程。

        实测：给控件 ``set_focus`` 之后，``GetForegroundWindow`` 返回的是
        那个 **Edit 控件句柄**，不是顶层窗口。所以这里按进程比对，
        而不是拿顶层窗口句柄硬比——后者会误判、把正常下单拦下来。
        """
        import win32gui
        import win32process

        foreground = win32gui.GetForegroundWindow()
        if not foreground:
            raise exceptions.TradeError("当前没有前台窗口，键入无处可去，已中止")
        try:
            pid = win32process.GetWindowThreadProcessId(foreground)[1]
        except Exception:
            pid = 0
        if pid != self._pid:
            raise exceptions.TradeError(
                "前台窗口不属于下单进程（前台 pid=%s，下单 pid=%s），"
                "键入会落到别的程序上，已中止" % (pid, self._pid)
            )
        return True

    # ------------------------------------------------------------------
    # 控件定位
    # ------------------------------------------------------------------
    def find(self, control_id, class_name=None, visible=True):
        """返回**最后一个**匹配的可见控件。

        同花顺把买入、卖出、撤单等页面一次性建好叠在一起，同一个
        control_id 会出现两三份，只有当前页那份是可见的。pywinauto 的
        ``child_window(control_id=...)`` 取第一个匹配项，很可能拿到隐藏
        页面上的控件——这正是之前"写入成功但读回为空"的来源之一。
        """
        found = 0
        for hwnd in self._n.children(self.root):
            try:
                if self._n.control_id(hwnd) != control_id:
                    continue
                if class_name and self._n.class_name(hwnd) != class_name:
                    continue
                if visible and not self._n.is_visible(hwnd):
                    continue
                found = hwnd
            except Exception:
                continue
        return found or None

    def _require(self, control_id, class_name, what):
        hwnd = self.find(control_id, class_name)
        if not hwnd:
            raise exceptions.TradeError(
                "找不到%s控件（control_id=%s），当前页面可能不是交易页"
                % (what, control_id)
            )
        return hwnd

    # ------------------------------------------------------------------
    # 字段写入
    # ------------------------------------------------------------------
    def clear_field(self, control_id):
        """清空字段。

        同花顺的价格框会预填一个值，且 ``Ctrl+A`` 会被拦截，全选无效。
        只能 ``End`` 之后逐键退格，把已有内容一个个删掉。
        """
        from pywinauto.controls.win32_controls import EditWrapper

        hwnd = self._require(control_id, "Edit", "输入")
        editor = EditWrapper(hwnd)
        editor.set_focus()
        time.sleep(0.12)
        editor.type_keys("{END}", pause=0.02)
        # 数量级远超任何真实价格/数量的位数，多删无副作用
        editor.type_keys("{BACKSPACE}" * 24, pause=0.01)
        time.sleep(0.12)
        return hwnd

    def type_field(self, control_id, text):
        """清空后键入，逐字符真实按键。"""
        from pywinauto.controls.win32_controls import EditWrapper

        hwnd = self.clear_field(control_id)
        editor = EditWrapper(hwnd)
        editor.set_focus()
        editor.type_keys(str(text), with_spaces=False, pause=0.06)
        time.sleep(0.15)
        return hwnd

    def stock_name(self):
        """当前证券名称，空字符串表示代码还没被客户端接受。"""
        hwnd = self.find(CONTROL_STOCK_NAME, "Static")
        if not hwnd:
            return ""
        return (self._n.text(hwnd) or "").strip()

    def wait_stock_name(self, timeout=3.0):
        """等待客户端查询出证券简称。

        这是唯一可信的"代码已写入并被接受"的证据——字段本身读不回来。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            name = self.stock_name()
            if name and name not in ("?", "-"):
                return name
            time.sleep(0.05)
        raise exceptions.TradeError(
            "证券代码未被客户端接受（证券名称仍为空），已中止委托"
        )

    def refill(self):
        """点“重填”把当前页恢复成空表单。"""
        hwnd = self.find(CONTROL_REFILL, "Button")
        if hwnd:
            self._n.post_message(hwnd, BM_CLICK)
            time.sleep(0.4)

    def select_page(self, path):
        """按左侧菜单路径切页（定向消息，不模拟鼠标）。"""
        if not self.tree:
            raise exceptions.TradeError("缺少左侧菜单句柄，无法切换交易页面")
        from pywinauto.controls.common_controls import TreeViewWrapper

        item = TreeViewWrapper(self.tree).get_item(list(path))
        if item is None:
            raise exceptions.TradeError("菜单项不存在: %s" % " / ".join(path))
        self._n.send_timeout(self.tree, TVM_SELECTITEM, TVGN_CARET, int(item.elem))
        time.sleep(0.6)

    # ------------------------------------------------------------------
    # 弹窗
    # ------------------------------------------------------------------
    def dialogs(self):
        """当前属于下单进程的可见 #32770 弹窗。"""
        import win32gui
        import win32process

        found = []

        def collect(hwnd, _):
            try:
                if win32process.GetWindowThreadProcessId(hwnd)[1] != self._pid:
                    return True
                if win32gui.GetClassName(hwnd) != DIALOG_CLASS:
                    return True
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                found.append(int(hwnd))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(collect, None)
        return found

    def _dlg_item(self, hwnd, control_id):
        """GetDlgItem 的容错版：控件不存在时返回 0，不抛 pywin32 异常。"""
        try:
            return int(self._n.dlg_item(hwnd, control_id) or 0)
        except Exception:
            return 0

    def dialog_title(self, hwnd):
        control = self._dlg_item(hwnd, DIALOG_TITLE_CONTROL)
        if not control:
            return ""
        try:
            return (self._n.text(control) or "").strip()
        except Exception:
            return ""

    def dialog_body(self, hwnd):
        control = self._dlg_item(hwnd, DIALOG_BODY_CONTROL)
        if not control:
            return ""
        try:
            return _clean(self._n.text(control))
        except Exception:
            return ""

    def wait_dialog(self, titles, timeout=8.0, require_body=False):
        """等一个标题命中 titles 的弹窗，返回 (hwnd, 标题, 正文)。

        ``require_body=False`` 时必须立刻返回：``提示`` / ``撤单确认`` 这类
        弹窗的正文 Static **本来就是空的**，正文在自绘区域里。早先这里
        要求正文非空才返回，结果明明弹窗已经出来了却一直等到超时，
        上层只好误判成"没有弹窗"。
        """
        wanted = set(titles) if isinstance(titles, (list, tuple, set)) else {titles}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for hwnd in self.dialogs():
                try:
                    title = self.dialog_title(hwnd)
                except Exception:
                    continue
                if title not in wanted:
                    continue
                body = self.dialog_body(hwnd)
                if require_body and not body:
                    continue
                return hwnd, title, body
            time.sleep(0.08)
        return None, "", ""

    def post_click(self, control):
        """按控件。

        一律用 PostMessage 而不是 SendMessage：这些按钮会拉起模态对话框，
        同步等待会一直阻塞到弹窗关闭为止（实测 BM_CLICK 直接超时）。
        """
        self._n.post_message(control, BM_CLICK)
        return True

    def click_dialog(self, hwnd, button_id):
        """按弹窗按钮，同样走 PostMessage（见 post_click）。"""
        control = self._dlg_item(hwnd, button_id)
        if not control:
            raise exceptions.TradeError(
                "确认弹窗上没有找到按钮 %s" % button_id
            )
        self._n.post_message(control, BM_CLICK)
        return True

    def _safe_click(self, hwnd, button_id):
        """按弹窗按钮；没有该按钮时先按确定/关闭兜底，不抛异常。"""
        control = self._dlg_item(hwnd, button_id)
        if not control:
            return False
        self._n.post_message(control, BM_CLICK)
        return True

    def dismiss_leftovers(self):
        """提交前清掉遗留的广告/提示框。

        实测：客户端会不定时弹「成交价格预警服务」这类广告，标题恰好也是
        ``提示``，并且只有 ``确定``(id 2) 没有 ``是``(id 6)。如果不先清掉，
        提交后等确认框时会先撞上它，被误当成委托确认。
        """
        closed = 0
        for hwnd in self.dialogs():
            title = self.dialog_title(hwnd)
            if title in ("委托确认", "撤单确认"):
                continue
            if self._dismiss(hwnd):
                closed += 1
        return closed

    # ------------------------------------------------------------------
    # 提交与确认
    # ------------------------------------------------------------------
    def wait_confirm(self, timeout=8.0):
        """等真正的「委托确认」弹窗。

        不能只比标题：广告框、「提示」框、验证码框都可能带一个同名控件。
        真正的委托确认框有三个同时成立的特征：
        标题控件 1365 == ``委托确认``、正文控件 1040 非空、
        并且有 ``是(&Y)``(id 6) 按钮。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for hwnd in self.dialogs():
                try:
                    if self.dialog_title(hwnd) != "委托确认":
                        continue
                    body = self.dialog_body(hwnd)
                    if not body:
                        continue
                    if not self._dlg_item(hwnd, DIALOG_YES):
                        continue
                    return hwnd, body
                except Exception:
                    continue
            time.sleep(0.08)
        return None, ""

    def submit(self):
        """点提交按钮，返回确认弹窗快照。

        这里**只**负责把确认弹窗等出来；确认与否由调用方比对后决定。
        """
        self.dismiss_leftovers()
        hwnd = self._require(CONTROL_SUBMIT, "Button", "提交")
        self.assert_foreground()
        self.post_click(hwnd)

        dialog, body = self.wait_confirm()
        if not dialog:
            blocked, blocked_title, blocked_body = self.wait_dialog(
                ["提示", "提示信息"], timeout=1.5
            )
            if blocked:
                self._dismiss(blocked)
                raise exceptions.TradeError(
                    "客户端未进入委托确认，提示：%s" % (blocked_body or blocked_title)
                )
            raise exceptions.TradeError(
                "提交后没有出现委托确认弹窗，委托结果未知，请人工核对委托列表"
            )
        for other in self.dialogs():
            if other != dialog:
                self._dismiss(other)
        return dialog, ConfirmSnapshot(body)

    def confirm(self, dialog, timeout=8.0):
        """在确认弹窗上点“是”，尽力解析委托编号。

        实测：客户端的「提示」结果框正文是**自绘**的，``dialog_body``
        读不到合同编号（Static 一直为空）。所以本方法拿不到编号时返回
        ``{}`` 而不是抛错——委托编号由调用方回查委托列表确认，
        那里才是真正的权威来源。广告类提示（「成交价格预警服务」）会被
        识别并关掉，不会被误当成委托结果。
        """
        if not self._safe_click(dialog, DIALOG_YES):
            raise exceptions.TradeError(
                "委托确认弹窗上找不到“是”按钮，未提交，请人工核对"
            )
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            for hwnd in self.dialogs():
                try:
                    title = self.dialog_title(hwnd)
                except Exception:
                    continue
                if title not in ("提示", "提示信息"):
                    continue
                body = self.dialog_body(hwnd)
                if body:
                    seen.append(body[:80])
                    match = re.search(
                        r"(?:合同编号|委托编号|合同号|委托号)[:：]?\s*([0-9]{4,})",
                        body,
                    )
                    if match is None and "成功" in body:
                        match = re.search(r"[0-9]{4,}", body)
                    if match is not None:
                        self._dismiss(hwnd)
                        return {"entrust_no": match.group(0)}
                    # 有正文但没编号：多半是拒单原因
                    if "成功" in body or "失败" in body or "不足" in body:
                        self._dismiss(hwnd)
                        raise exceptions.TradeError(body)
                # 正文为空：广告或自绘结果框，关掉继续等
                self._dismiss(hwnd)
                break
            time.sleep(0.12)
        return {}
    def _dismiss(self, hwnd):
        """关掉一个提示框：优先“确定/是”，没有就发 WM_CLOSE。"""
        for button_id in (DIALOG_OK, DIALOG_YES, DIALOG_NO):
            control = self._dlg_item(hwnd, button_id)
            if control:
                self._n.post_message(control, BM_CLICK)
                break
        else:
            self._n.post_message(hwnd, WM_CLOSE)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if hwnd not in self.dialogs():
                return True
            time.sleep(0.05)
        return False

    def reject(self, dialog):
        """点“否”取消，不产生任何委托。"""
        try:
            self.click_dialog(dialog, DIALOG_NO)
        except Exception as exc:  # pragma: no cover
            logger.error("取消确认弹窗失败: %s", exc)
            return False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if dialog not in self.dialogs():
                return True
            time.sleep(0.05)
        return False
