# -*- coding: utf-8 -*-
import abc
import hashlib
import io
import os
import shutil
import tempfile
import time
from io import StringIO
from typing import TYPE_CHECKING, Dict, List, Optional

import pandas as pd
import pywinauto
import pywinauto.clipboard
import pywinauto.keyboard

from easytrader.log import logger
from easytrader.utils.captcha import (
    captcha_recognize_improved,
    input_verify_code_manual,
)
from easytrader.utils.win_gui import (
    SetForegroundWindow,
    ShowWindow,
    win32defines,
)

if TYPE_CHECKING:
    # pylint: disable=unused-import
    from easytrader import clienttrader


def _foreground_window():
    """返回当前前台窗口句柄，取不到时返回 0。"""
    try:
        import win32gui

        return win32gui.GetForegroundWindow() or 0
    except Exception:
        return 0


def _restore_foreground(handle):
    """把前台还原到指定窗口。

    交易客户端在弹验证码、切换页签时会主动抢前台；读取结束后若不复位，
    用户的输入焦点就被留在行情软件上，正在打的字会跑到别处。
    这里用 AttachThreadInput 绕过前台锁定限制，失败也不抛异常。
    """
    if not handle:
        return False
    try:
        import win32gui
        import win32process

        if win32gui.GetForegroundWindow() == handle:
            return True

        current_thread = win32process.GetWindowThreadProcessId(
            win32gui.GetForegroundWindow()
        )[0]
        target_thread = win32process.GetWindowThreadProcessId(handle)[0]
        attached = False
        if current_thread != target_thread:
            try:
                win32process.AttachThreadInput(
                    current_thread, target_thread, True
                )
                attached = True
            except Exception:
                attached = False
        try:
            win32gui.SetForegroundWindow(handle)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(
                        current_thread, target_thread, False
                    )
                except Exception:
                    pass
        return win32gui.GetForegroundWindow() == handle
    except Exception:
        logger.debug("还原前台窗口失败", exc_info=True)
        return False


def _top_level_windows_of(app) -> List[int]:
    """返回指定应用进程的所有顶层窗口句柄（按 Z 序，从前到后）。"""
    process_id = None
    try:
        process_id = app.process
    except Exception:
        return []

    handles = []
    try:
        import win32gui
        import win32process

        def _collect(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == process_id:
                    handles.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_collect, None)
    except Exception:
        logger.debug("枚举顶层窗口失败", exc_info=True)
    return handles


def _drop_empty_rows(df: "pd.DataFrame") -> List[Dict]:
    """丢弃全空占位行。

    客户端的虚拟表格在没有数据时仍会导出 1~2 行占位行，
    这些行的文本列是空字符串、数值列是 0（如 委托数量=0, 委托价格=0.000），
    直接转 dict 会得到看似合法的假记录，误导调用方以为存在委托/持仓。
    """
    if df.empty:
        return []

    def is_blank(value) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        # NaN / NaT
        try:
            if pd.isna(value):
                return True
        except (TypeError, ValueError):
            pass
        # 数值 0 视为空占位
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) == 0.0
        return False

    keep = [
        not all(is_blank(value) for value in row)
        for row in df.itertuples(index=False)
    ]
    return df[keep].to_dict("records")


class IGridStrategy(abc.ABC):
    @abc.abstractmethod
    def get(self, control_id: int) -> List[Dict]:
        """
        获取 grid 数据并格式化返回

        :param control_id: grid 的 control id
        :return: grid 数据
        """
        pass

    @abc.abstractmethod
    def set_trader(self, trader: "clienttrader.IClientTrader"):
        pass


class BaseStrategy(IGridStrategy):
    """表格抓取策略基类。

    这里集中放各策略共用的验证码处理逻辑，避免 Copy / Xls 各写一份实现，
    也避免两份实现的行为逐渐走偏（早期 Xls 用 Ctrl+A+Delete 清空验证码框，
    而该操作在本客户端上无效，会导致重试时内容不断累积）。
    """

    # 验证码弹窗控件 ID（各券商客户端一致）
    CAPTCHA_IMAGE_CONTROL_ID = 0x965
    CAPTCHA_EDIT_CONTROL_ID = 0x964
    CAPTCHA_ERROR_CONTROL_ID = 0x966
    CAPTCHA_OK_BUTTON_ID = 1
    CAPTCHA_CANCEL_BUTTON_ID = 2

    def _captcha_image(self, dialog):
        """验证码图片控件。"""
        return dialog.window(
            control_id=self.CAPTCHA_IMAGE_CONTROL_ID, class_name="Static"
        )

    def _captcha_editor(self, dialog):
        """验证码输入框控件。"""
        return dialog.window(
            control_id=self.CAPTCHA_EDIT_CONTROL_ID, class_name="Edit"
        )

    def _captcha_button(self, dialog, control_id):
        """验证码弹窗上的按钮（确定/取消）。"""
        return dialog.window(control_id=control_id, class_name="Button")

    def _capture_control(self, control):
        """抓取控件图像，返回 PIL Image。

        优先用 PrintWindow：窗口被遮挡、被移到屏幕外，或运行在隐藏桌面时，
        ImageGrab / capture_as_image 依赖真实屏幕会抛 "screen grab failed"，
        而 PrintWindow 直接把内容画到内存 DC，不依赖窗口是否显示在屏幕上。
        失败时回退到 capture_as_image，兼容不支持 PrintWindow 的环境。
        """
        from easytrader import screenshot

        hwnd = control.wrapper_object().handle
        try:
            screenshot.ensure_rendered(hwnd)
            return screenshot.print_window(hwnd)
        except Exception:
            logger.debug("PrintWindow 抓图失败，改用 capture_as_image", exc_info=True)
            return control.capture_as_image()

    def __init__(self):
        self._trader = None

    def set_trader(self, trader: "clienttrader.IClientTrader"):
        self._trader = trader

    @abc.abstractmethod
    def get(self, control_id: int) -> List[Dict]:
        """
        :param control_id: grid 的 control id
        :return: grid 数据
        """
        pass

    @staticmethod
    def _fill_captcha(editor, code):
        """把验证码写入输入框。

        两个实测得到的硬约束，缺一就会导致验证码永远失败：

        1. 该控件对 set_edit_text / WM_SETTEXT 无效，写入后内容仍为空，
           必须用 type_keys 模拟键盘输入；
        2. Ctrl+A / Delete 在该控件上同样无效，清不掉上一次的错误内容。
           若不先真正清空，重试时会在旧内容后面继续追加，
           例如 "R2ku" + "R2ku" 变成 "R2kuR2ku"，每次都必然被拒绝。
           只有 BACKSPACE 能逐字符删除，所以这里按足够次数退格。
        """
        editor.set_focus()
        # 验证码只有 4 位，退格次数留足余量以清掉历史累积内容。
        # 16 次退格合并成一次 type_keys 发送，避免每个按键的往返开销。
        editor.type_keys("{BACKSPACE 16}", set_foreground=False, pause=0.005)
        editor.type_keys(code, set_foreground=False, pause=0.01)

    def _set_foreground(self, control=None):
        try:
            if control is None:
                control = self._trader.main
            # 既可能传入控件包装对象，也可能传入 element info
            window = (
                control.wrapper_object()
                if hasattr(control, "wrapper_object")
                else control
            )
            if control.has_style(win32defines.WS_MINIMIZE):  # if minimized
                ShowWindow(window, 9)  # restore window state
            else:
                SetForegroundWindow(window)  # bring to front
        except Exception:
            logger.debug("置前窗口失败", exc_info=True)

    def _get_grid(self, control_id: int):
        """返回当前唯一可见的表格控件。

        交易客户端会在后台保留多个同 control_id 的表格控件（每个查询页一个），
        直接 child_window 查找会命中多个而抛出 ElementAmbiguousError，
        这里按可见性过滤，避免在页面切换后静默失败。
        """
        candidates = []
        for control in self._trader.main.descendants():
            try:
                if (
                    control.class_name() == "CVirtualGridCtrl"
                    and control.control_id() == control_id
                    and control.is_visible()
                ):
                    candidates.append(control)
            except Exception:  # 控件可能在遍历过程中销毁
                continue

        if len(candidates) == 1:
            return candidates[0]

        if not candidates:
            raise RuntimeError(
                "未找到可见的表格控件 (control_id={})".format(control_id)
            )

        raise RuntimeError(
            "存在 {} 个可见的表格控件 (control_id={})，无法确定目标".format(
                len(candidates), control_id
            )
        )


class Copy(BaseStrategy):
    """
    通过复制 grid 内容到剪切板再读取来获取 grid 内容
    """

    _need_captcha_reg = True

    def get(self, control_id: int) -> List[Dict]:
        grid = self._get_grid(control_id)
        # 整个读取过程会短暂激活交易窗口（切菜单、复制、验证码弹窗），
        # 结束后把前台还给用户原本的窗口，避免打断用户正在做的事。
        original_foreground = _foreground_window()
        try:
            self._set_foreground(grid)
            # 先清空剪贴板，否则复制失败时会读到上一次的旧数据
            try:
                pywinauto.clipboard.EmptyClipboard()
            except Exception:
                logger.debug("清空剪贴板失败", exc_info=True)

            grid.set_focus()
            # pause 会按"每个按键"累加等待：^A^C 共 5 个按键，
            # pause=0.2 时仅这一步就要 1.2 秒，是整条链路最大的单点开销。
            grid.type_keys("^A^C", set_foreground=False, pause=0.02)

            content = self._get_clipboard_data()
            return self._format_grid_data(content)
        finally:
            _restore_foreground(original_foreground)

    def _format_grid_data(self, data: str) -> List[Dict]:
        try:
            df = pd.read_csv(
                io.StringIO(data),
                delimiter="\t",
                dtype=self._trader.config.GRID_DTYPE,
                na_filter=False,
            )
            return _drop_empty_rows(df)
        except Exception:
            logger.exception("解析表格内容失败")
            Copy._need_captcha_reg = True
            raise

    def _get_captcha_dialog(self):
        """返回验证码弹窗，未出现时返回 None。

        该弹窗没有窗口标题（标题栏文本在 Static 控件里），因此不能按 title 查找；
        而且它可能被主交易窗口盖住（不是 top_window），所以遍历进程内所有顶层窗口，
        用「验证码图片控件」这一特征来识别。
        """
        for handle in _top_level_windows_of(self._trader.app):
            try:
                window = self._trader.app.window(handle=handle)
                if self._captcha_image(window).exists(timeout=0.05):
                    return window
            except Exception:
                continue
        return None

    def _wait_captcha_dialog(self, timeout=2.0, interval=0.02):
        """复制被拦截时验证码弹窗是异步弹出的，需要等待。

        用细粒度轮询替代 wait(0.1)：弹窗通常在几十毫秒内就出现，
        粗粒度等待平均会多花半轮的时间。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            dialog = self._get_captcha_dialog()
            if dialog is not None:
                return dialog
            time.sleep(interval)
        return None

    def _solve_captcha(self, max_retry=8):
        """处理验证码弹窗，返回 True 表示已通过。

        单次 OCR 准确率约 8 成，因此识别失败（空结果）时应直接换一张验证码，
        而不是把空内容提交上去白白消耗一次机会。
        """
        dialog = self._wait_captcha_dialog()
        if dialog is None:
            # 没有弹窗，说明本次复制没有被拦截
            return True

        attempts = 0
        while attempts < max_retry:
            dialog = self._get_captcha_dialog()
            if dialog is None:
                return True

            attempts += 1
            try:
                # 弹窗可能被主窗口盖住，提到前台以便真实键盘输入生效
                self._set_foreground(dialog)

                image_path = os.path.join(
                    tempfile.gettempdir(), "easytrader_captcha.png"
                )
                self._capture_control(self._captcha_image(dialog)).save(
                    image_path
                )

                captcha_num = captcha_recognize_improved(image_path).strip()
                captcha_num = "".join(captcha_num.split())
                logger.info("第 %s 次识别验证码 -> %s", attempts, captcha_num)

                if len(captcha_num) != 4:
                    # 识别不出结果：直接换一张，不要提交空值
                    logger.warning("验证码识别未得到 4 位结果，换一张重试")
                    self._refresh_captcha(dialog)
                    continue

                self._fill_captcha(self._captcha_editor(dialog), captcha_num)
                self._captcha_button(dialog, self.CAPTCHA_OK_BUTTON_ID).click()

                # 轮询等待结果，而不是固定 sleep：
                # 通过时弹窗消失，被拒时弹窗留在原地并显示"验证码错误!!"。
                # 只要看到错误提示就可以立刻重试，不必等满超时。
                outcome = self._wait_captcha_outcome(dialog, timeout=2.0)
                if outcome == "passed":
                    logger.info("验证码通过：%s", captcha_num)
                    return True

                if outcome == "rejected":
                    logger.warning("验证码被拒绝：%s", captcha_num)
                else:
                    logger.warning("验证码结果未知，按拒绝处理：%s", captcha_num)
                # 被拒后客户端不会自动换图，必须主动刷新，否则会重复提交同一张
                self._refresh_captcha(self._get_captcha_dialog())
            except Exception:
                logger.exception("第 %s 次验证码处理异常", attempts)
                self._trader.wait(0.2)

        # 放弃本次操作，点取消关掉弹窗，避免主流程卡住
        logger.error("验证码连续 %s 次失败，取消本次数据获取", max_retry)
        dialog = self._get_captcha_dialog()
        if dialog is not None:
            try:
                self._captcha_button(
                    dialog, self.CAPTCHA_CANCEL_BUTTON_ID
                ).click()
            except Exception:
                logger.debug("点击取消失败", exc_info=True)
        return False

    def _wait_captcha_closed(self, timeout=2.0, interval=0.03):
        """轮询等待验证码弹窗消失，返回是否已通过。

        比固定 sleep 快得多：提交后通常几十毫秒就有结果，
        而固定等待必须等到设定时间才能继续。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_captcha_dialog() is None:
                return True
            time.sleep(interval)
        return False

    def _wait_captcha_outcome(self, dialog, timeout=2.0, interval=0.03):
        """等待验证码提交结果：passed / rejected / timeout。

        客户端受理后弹窗直接关闭；被拒则弹窗保留并显示"验证码错误!!"。
        尽早识别拒绝可以立刻换一张重试，省掉盲等超时的时间。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_captcha_dialog() is None:
                return "passed"
            if self._captcha_error_shown(dialog):
                return "rejected"
            time.sleep(interval)
        return "timeout"

    def _captcha_error_shown(self, dialog):
        """弹窗里是否已经显示出"验证码错误"提示。"""
        try:
            label = dialog.window(
                control_id=self.CAPTCHA_ERROR_CONTROL_ID, class_name="Static"
            )
            return bool(label.exists(timeout=0)) and label.is_visible()
        except Exception:
            return False

    def _refresh_captcha(self, dialog):
        """点图片刷新验证码，并确认图片真的变了。

        被拒绝后客户端不会自动换图，如果刷新失败还继续提交，
        就会反复提交同一张验证码，白白耗尽重试次数。
        """
        if dialog is None:
            return False
        try:
            image = self._captcha_image(dialog)
            before = self._captcha_image_digest(image)
            image.click()
            self._trader.wait(0.4)
            after = self._captcha_image_digest(image)
            if before and after and before == after:
                # 再试一次，个别情况下第一次点击不生效
                image.click()
                self._trader.wait(0.4)
                after = self._captcha_image_digest(image)
            changed = bool(before and after and before != after)
            if not changed:
                logger.warning("刷新验证码后图片未变化")
            return changed
        except Exception:
            logger.debug("刷新验证码图片失败", exc_info=True)
            return False

    def _captcha_image_digest(self, image):
        """取验证码图片的指纹，用于判断是否已刷新。"""
        try:
            buffer = io.BytesIO()
            self._capture_control(image).save(buffer, format="PNG")
            return hashlib.md5(buffer.getvalue()).hexdigest()
        except Exception:
            return ""

    def _get_clipboard_data(self) -> str:
        if Copy._need_captcha_reg and not self._solve_captcha():
            raise RuntimeError("验证码未能通过，无法复制表格数据")

        count = 5
        last_error = None
        while count > 0:
            try:
                return pywinauto.clipboard.GetData()
            # pylint: disable=broad-except
            except Exception as e:
                last_error = e
                count -= 1
                self._trader.wait(0.2)
                logger.warning("读取剪贴板失败，重试中: %s", e)
        raise RuntimeError(
            "复制表格后无法从剪贴板读取数据（可能验证码未通过或表格为空）"
        ) from last_error


class WMCopy(Copy):
    """
    通过复制 grid 内容到剪切板再读取来获取 grid 内容
    """

    def get(self, control_id: int) -> List[Dict]:
        grid = self._get_grid(control_id)
        grid.post_message(win32defines.WM_COMMAND, 0xE122, 0)
        self._trader.wait(0.1)
        content = self._get_clipboard_data()
        return self._format_grid_data(content)


class Xls(BaseStrategy):
    """将虚拟表格导出为临时 TSV 文件并解析。"""

    CAPTCHA_CONFIG = {
        "max_retry_count": 5,
        "input_wait_time": 0.1,
        "refresh_wait_time": 0.2,
        "success_timeout": 2.0,
        "manual_input": False,
        "save_debug_samples": False,
        "tesseract_path": None,
    }

    CAPTCHA_MODES = {
        "fast": {"max_retry_count": 3, "success_timeout": 1.0},
        "balanced": {"max_retry_count": 5, "success_timeout": 2.0},
        "accurate": {"max_retry_count": 8, "success_timeout": 3.0},
    }

    def __init__(self, tmp_folder: Optional[str] = None):
        super().__init__()
        self.tmp_folder = tmp_folder
        self.captcha_config = dict(self.CAPTCHA_CONFIG)

    def set_captcha_mode(self, mode="balanced"):
        if mode not in self.CAPTCHA_MODES:
            raise ValueError("不支持的验证码模式：{}".format(mode))
        self.captcha_config.update(self.CAPTCHA_CONFIG)
        self.captcha_config.update(self.CAPTCHA_MODES[mode])

    def get_captcha_config(self):
        return dict(self.captcha_config)

    def update_captcha_config(self, **kwargs):
        import math

        settings = dict(kwargs)
        mode = settings.pop("mode", None)
        if mode is not None and mode not in self.CAPTCHA_MODES:
            raise ValueError("不支持的验证码模式：{}".format(mode))

        allowed = set(self.CAPTCHA_CONFIG)
        unknown = set(settings) - allowed
        if unknown:
            raise ValueError("未知的验证码配置项：{}".format(sorted(unknown)))

        if "max_retry_count" in settings:
            value = settings["max_retry_count"]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 10
            ):
                raise ValueError("验证码重试次数必须是 1 到 10 的整数")

        for key in ("input_wait_time", "refresh_wait_time", "success_timeout"):
            if key in settings:
                try:
                    value = float(settings[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError("{} 必须是数字".format(key)) from exc
                if not math.isfinite(value) or not 0 <= value <= 10:
                    raise ValueError("{} 必须在 0 到 10 秒之间".format(key))
                settings[key] = value

        for key in ("manual_input", "save_debug_samples"):
            if key in settings and not isinstance(settings[key], bool):
                raise ValueError("{} 必须是布尔值".format(key))

        if "tesseract_path" in settings:
            value = settings["tesseract_path"]
            if value is not None and not isinstance(value, str):
                raise ValueError("tesseract_path 必须是字符串或 null")

        if mode is not None:
            self.set_captcha_mode(mode)
        self.captcha_config.update(settings)

    def configure_captcha(self, settings):
        if not isinstance(settings, dict):
            raise TypeError("验证码配置必须是 JSON 对象")
        self.update_captcha_config(**settings)

    def _captcha_visible(self, timeout=0.1):
        try:
            return self._trader.app.top_window().window(
                class_name="Static", title_re="验证码"
            ).exists(timeout=timeout)
        except Exception:
            return False

    def _save_dialog(self):
        app = self._trader.app
        try:
            window = app.window(title_re="另存为|Save As|文件另存为")
            if window.exists(timeout=0.1):
                return window
        except Exception:
            pass
        try:
            window = app.top_window()
            window_title = window.window_text()
            if "另存为" in window_title or "Save As" in window_title:
                return window
        except Exception:
            pass
        return None

    def _handle_captcha(self):
        for attempt in range(self.captcha_config["max_retry_count"]):
            image_path = None
            try:
                handle = tempfile.mkstemp(suffix=".png")
                image_path = handle[1]
                os.close(handle[0])
                captcha = self._captcha_image(self._trader.app.top_window())
                self._capture_control(captcha).save(image_path)

                if self.captcha_config["save_debug_samples"]:
                    debug_dir = os.path.join(os.getcwd(), "captcha_debug")
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_path = os.path.join(
                        debug_dir, "captcha_attempt_{}.png".format(attempt + 1)
                    )
                    shutil.copyfile(image_path, debug_path)

                if self.captcha_config["manual_input"]:
                    code = input_verify_code_manual(image_path).strip()
                else:
                    code = captcha_recognize_improved(
                        image_path,
                        tesseract_path=self.captcha_config["tesseract_path"],
                    )

                if len(code) != 4 or not code.isalnum():
                    logger.warning("验证码识别未得到 4 位有效结果")
                else:
                    top_window = self._trader.app.top_window()
                    editor = self._captcha_editor(top_window)
                    self._fill_captcha(editor, code)
                    self._trader.app.top_window().type_keys(
                        "{ENTER}", set_foreground=False
                    )
                    wait_steps = max(
                        1, int(self.captcha_config["success_timeout"] / 0.1)
                    )
                    for _ in range(wait_steps):
                        self._trader.wait(0.1)
                        if not self._captcha_visible(timeout=0.05):
                            logger.info("验证码已通过")
                            return

                # 未通过或被拒绝时刷新验证码，重新识别
                if self._captcha_visible(timeout=0.1):
                    captcha.click()
                    self._trader.wait(self.captcha_config["refresh_wait_time"])
            except Exception as exc:
                logger.warning("第 %s 次验证码处理失败：%s", attempt + 1, exc)
            finally:
                if image_path and os.path.exists(image_path):
                    os.remove(image_path)

        try:
            self._captcha_button(
                self._trader.app.top_window(), self.CAPTCHA_CANCEL_BUTTON_ID
            ).click()
        except Exception:
            logger.debug("点击取消失败", exc_info=True)
        raise RuntimeError("验证码多次识别失败")

    def get(self, control_id: int) -> List[Dict]:
        try:
            grid = self._get_grid(control_id)
            if not grid.exists():
                raise RuntimeError("无法获取指定的表格控件")
        except Exception as exc:
            grids = self._trader.main.child_windows(
                class_name="CVirtualGridCtrl"
            )
            if not grids:
                raise RuntimeError("未找到虚拟表格控件") from exc
            grid = grids[0]
            logger.warning("使用备用表格控件 %s", grid.control_id())

        grid.click()
        self._set_foreground(grid)
        grid.set_focus()
        grid.type_keys("^s", set_foreground=False)

        save_window = None
        for _ in range(60):
            if self._captcha_visible():
                self._handle_captcha()
                continue
            save_window = self._save_dialog()
            if save_window is not None:
                break
            self._trader.wait(0.1)
        if save_window is None:
            raise TimeoutError("等待表格导出窗口超时")

        if self.tmp_folder:
            os.makedirs(self.tmp_folder, exist_ok=True)
        handle = tempfile.mkstemp(suffix=".xls", dir=self.tmp_folder)
        temp_path = handle[1]
        os.close(handle[0])
        os.remove(temp_path)

        try:
            edit = None
            for control_id in (0x47C, 0x3E9, 1001):
                candidate = save_window.child_window(
                    class_name="Edit", control_id=control_id
                )
                if candidate.exists(timeout=0.1):
                    edit = candidate
                    break
            if edit is None:
                candidate = save_window.child_window(class_name="Edit")
                if candidate.exists(timeout=0.1):
                    edit = candidate
            if edit is None:
                raise RuntimeError("找不到导出文件名输入框")

            self._set_foreground(save_window)
            edit.set_edit_text(temp_path)
            save_window.type_keys("%{s}", set_foreground=False)

            previous_size = -1
            stable_checks = 0
            for _ in range(60):
                if os.path.isfile(temp_path):
                    size = os.path.getsize(temp_path)
                    if size > 0 and size == previous_size:
                        stable_checks += 1
                        if stable_checks >= 2:
                            break
                    else:
                        stable_checks = 0
                    previous_size = size
                self._trader.wait(0.1)

            if (
                not os.path.isfile(temp_path)
                or os.path.getsize(temp_path) == 0
            ):
                raise FileNotFoundError("未生成表格导出文件")
            return self._format_grid_data(temp_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _format_grid_data(self, data: str) -> List[Dict]:
        try:
            with open(data, encoding="gbk", errors="replace") as f:
                content = f.read()
            df = pd.read_csv(
                StringIO(content),
                delimiter="\t",
                dtype=self._trader.config.GRID_DTYPE,
                na_filter=False,
            )
            return _drop_empty_rows(df)
        except Exception as exc:
            logger.exception("解析导出的表格失败")
            raise ValueError("表格导出文件无法解析") from exc
