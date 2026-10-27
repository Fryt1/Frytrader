# coding:utf-8
import re
import time
from typing import Optional

from easytrader import exceptions
from easytrader.utils.perf import perf_clock
from easytrader.utils.win_gui import SetForegroundWindow, ShowWindow, win32defines


class PopDialogHandler:
    def __init__(self, app):
        self._app = app

    @staticmethod
    def _set_foreground(window):
        if window.has_style(win32defines.WS_MINIMIZE):  # if minimized
            ShowWindow(window.wrapper_object(), 9)  # restore window state
        else:
            SetForegroundWindow(window.wrapper_object())  # bring to front

    @perf_clock
    def handle(self, title):
        if any(s in title for s in {"提示信息", "委托确认", "网上交易用户协议", "撤单确认"}):
            self._submit_by_shortcut()
            return None

        if "提示" in title:
            content = self._extract_content()
            self._submit_by_click()
            return {"message": content}

        content = self._extract_content()
        self._close()
        return {"message": "unknown message: {}".format(content)}

    def _extract_content(self):
        return self._app.top_window().Static.window_text()

    @staticmethod
    def _extract_entrust_id(content):
        return re.search(r"[\da-zA-Z]+", content).group()

    def _submit_by_click(self):
        try:
            self._app.top_window()["确定"].click()
        except Exception as ex:
            self._app.Window_(best_match="Dialog", top_level_only=True).ChildWindow(
                best_match="确定"
            ).click()

    def _submit_by_shortcut(self):
        self._set_foreground(self._app.top_window())
        self._app.top_window().type_keys("%Y", set_foreground=False)

    def _close(self):
        self._app.top_window().close()


class TradePopDialogHandler(PopDialogHandler):
    @perf_clock
    def handle(self, title) -> Optional[dict]:
        if title == "委托确认":
            self._submit_by_shortcut()
            return None

        if title == "提示信息":
            content = self._extract_content()
            if "超出涨跌停" in content:
                self._submit_by_shortcut()
                return None

            if "委托价格的小数价格应为" in content:
                self._submit_by_shortcut()
                return None

            if "逆回购" in content:
                self._submit_by_shortcut()
                return None

            if "正回购" in content:
                self._submit_by_shortcut()
                return None

            # 无法识别的提示信息：关闭它并明确回报内容，
            # 既不能当作成功，也不能留在界面上让上层反复遇到同一个弹窗。
            self._close()
            return {"message": content}

        if title == "提示":
            content = self._extract_content()
            if "成功" in content:
                match = re.search(r"[\da-zA-Z]+", content)
                entrust_no = match.group() if match else ""
                self._submit_by_click()
                if not entrust_no:
                    raise exceptions.TradeError(
                        "提示成功但未解析到委托编号: {}".format(content)
                    )
                return {"entrust_no": entrust_no}

            self._submit_by_click()
            time.sleep(0.05)
            raise exceptions.TradeError(content)

        # 其它弹窗：关闭并回报，避免上层反复遇到同一个未处理弹窗
        content = self._extract_content()
        self._close()
        return {
            "message": "unknown message: {}({})".format(title, content),
            "result": "unknown",
        }
