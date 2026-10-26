# -*- coding: utf-8 -*-
import abc
import io
import tempfile
from io import StringIO
from typing import TYPE_CHECKING, Dict, List, Optional

import pandas as pd
import pywinauto.keyboard
import pywinauto
import pywinauto.clipboard

from easytrader.log import logger
from easytrader.utils.captcha import (
    captcha_recognize_improved,
    input_verify_code_manual,
)
from easytrader.utils.win_gui import SetForegroundWindow, ShowWindow, win32defines

if TYPE_CHECKING:
    # pylint: disable=unused-import
    from easytrader import clienttrader


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

    def _get_grid(self, control_id: int):
        grid = self._trader.main.child_window(
            control_id=control_id, class_name="CVirtualGridCtrl"
        )
        return grid

    def _set_foreground(self, grid=None):
        try:
            if grid is None:
                grid = self._trader.main
            if grid.has_style(win32defines.WS_MINIMIZE):  # if minimized
                ShowWindow(grid.wrapper_object(), 9)  # restore window state
            else:
                SetForegroundWindow(grid.wrapper_object())  # bring to front
        except:
            pass


class Copy(BaseStrategy):
    """
    通过复制 grid 内容到剪切板再读取来获取 grid 内容
    """

    _need_captcha_reg = True

    def get(self, control_id: int) -> List[Dict]:
        grid = self._get_grid(control_id)
        self._set_foreground(grid)
        grid.type_keys("^A^C", set_foreground=False, pause=0.2)
        content = self._get_clipboard_data()
        return self._format_grid_data(content)

    def _format_grid_data(self, data: str) -> List[Dict]:
        try:
            df = pd.read_csv(
                io.StringIO(data),
                delimiter="\t",
                dtype=self._trader.config.GRID_DTYPE,
                na_filter=False,
            )
            return df.to_dict("records")
        except:
            Copy._need_captcha_reg = True

    def _get_clipboard_data(self) -> str:
        if Copy._need_captcha_reg:
            if (
                    self._trader.app.top_window().window(class_name="Static", title_re="验证码").exists(timeout=1)
            ):
                file_path = "tmp.png"
                count = 5
                found = False
                while count > 0:
                    self._trader.app.top_window().window(
                        control_id=0x965, class_name="Static"
                    ).capture_as_image().save(
                        file_path
                    )  # 保存验证码

                    captcha_num = captcha_recognize_improved(file_path).strip()
                    captcha_num = "".join(captcha_num.split())
                    logger.info("captcha result-->" + captcha_num)
                    if len(captcha_num) == 4:
                        editor = self._trader.app.top_window().window(
                            control_id=0x964, class_name="Edit"
                        ) # 验证码输入框
                        editor.set_focus() # 焦点移到验证码输入框 (也可不聚焦防止键盘误触输入，不聚焦type_edit_control_keys也可正常输入)
                        self._trader.wait(0.1) # 输入前短暂等待
                        self._trader.type_edit_control_keys(
                            editor,
                            captcha_num
                        )  # 模拟输入验证码

                        self._trader.wait(0.1) # 输完后短暂等待
                        self._trader.app.top_window().type_keys("{ENTER}", pause=0.1)  # 模拟发送enter，点击确定
                        if not editor.exists(timeout=1):  # 窗体消失
                            logger.info("验证码验证成功-->" + captcha_num)
                            found = True
                            break
                    count -= 1
                    self._trader.wait(0.1)
                    self._trader.app.top_window().window(
                        control_id=0x965, class_name="Static"
                    ).click()
                if not found:
                    self._trader.app.top_window().Button2.click()  # 点击取消
            else:
                pass
                # 不要将 Copy._need_captcha_reg 置为 False, 因为它是类方法, 一旦置为 False, 后续操作都不再进行验证码识别
                # Copy._need_captcha_reg = False
        count = 5
        while count > 0:
            try:
                return pywinauto.clipboard.GetData()
            # pylint: disable=broad-except
            except Exception as e:
                count -= 1
                logger.exception("%s, retry ......", e)


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
        import os
        import shutil

        for attempt in range(self.captcha_config["max_retry_count"]):
            image_path = None
            try:
                handle = tempfile.mkstemp(suffix=".png")
                image_path = handle[1]
                os.close(handle[0])
                captcha = self._trader.app.top_window().window(
                    control_id=0x965, class_name="Static"
                )
                captcha.capture_as_image().save(image_path)

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
                    editor = self._trader.app.top_window().window(
                        control_id=0x964, class_name="Edit"
                    )
                    self._trader.type_edit_control_keys(editor, code)
                    self._trader.app.top_window().type_keys(
                        "{ENTER}", set_foreground=False
                    )
                    wait_steps = max(
                        1, int(self.captcha_config["success_timeout"] / 0.1)
                    )
                    for _ in range(wait_steps):
                        self._trader.wait(0.1)
                        if not self._captcha_visible():
                            logger.info("验证码已通过")
                            return

                if self._captcha_visible():
                    captcha.click()
                    self._trader.wait(self.captcha_config["refresh_wait_time"])
            except Exception as exc:
                logger.warning("第 %s 次验证码处理失败：%s", attempt + 1, exc)
            finally:
                if image_path and os.path.exists(image_path):
                    os.remove(image_path)

        try:
            self._trader.app.top_window().Button2.click()
        except Exception:
            pass
        raise RuntimeError("验证码多次识别失败")

    def get(self, control_id: int) -> List[Dict]:
        import os

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
            return df.to_dict("records")
        except Exception as exc:
            logger.exception("解析导出的表格失败")
            raise ValueError("表格导出文件无法解析") from exc
