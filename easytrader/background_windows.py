# -*- coding: utf-8 -*-
"""同花顺同桌面只读查询：隐藏主窗口，验证码允许临时前台。

普通菜单/复制/截图使用定向控件消息；只允许已验证的查询验证码显示。
不创建、移动、切换或删除任务视图桌面，不依赖 pyvda。
"""

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from easytrader.config.client import CommonConfig

VOYAGER_TITLE = "同花顺远航版"
TRADER_TITLE_PREFIX = "网上股票交易系统5.0"

QUERY_TREE_CLASS = "SysTreeView32"
QUERY_TREE_ID = 129
BALANCE_CLASS = "Static"
GRID_CLASS = "CVirtualGridCtrl"
CAPTCHA_DIALOG_CLASS = "#32770"
CAPTCHA_IMAGE_ID = 0x965
CAPTCHA_EDITOR_ID = 0x964
CAPTCHA_OK_ID = 1
CAPTCHA_CANCEL_ID = 2

WM_COMMAND = 0x0111
GRID_COPY_COMMAND = 0xE122
TVM_GETNEXTITEM = 0x110A
TVM_SELECTITEM = 0x110B
TVGN_CARET = 9

WS_VISIBLE = 0x10000000
GWL_STYLE = -16
GW_OWNER = 4
DWMWA_CLOAKED = 14
SWP_NO_ACTIVATE_SHOW = 0x0057
SMTO_ABORTIFHUNG = 0x0002
SEND_TIMEOUT_MS = 800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

@dataclass(frozen=True)
class CaptchaControls:
    """一次验证码对话框里已经定位好的控件句柄。"""

    dialog: int
    image: int
    editor: int
    ok: int
    cancel: int

    def __post_init__(self):
        for name in ("dialog", "image", "editor", "ok", "cancel"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("验证码控件 %s 必须是窗口句柄整数" % name)
            if value <= 0:
                raise ValueError("验证码控件 %s 必须是有效句柄" % name)


def _as_handle(value) -> int:
    handle = getattr(value, "handle", value)
    try:
        handle = int(handle)
    except (TypeError, ValueError):
        raise RuntimeError("窗口句柄无效: %r" % (value,)) from None
    if handle <= 0:
        raise RuntimeError("窗口句柄无效: %r" % (value,))
    return handle


def _layout_visible(native, hwnd: int, root: int) -> bool:
    """只看控件自身的 WS_VISIBLE 链，忽略遮挡、隐藏桌面和 DWM 遮蔽。"""
    seen = set()
    while hwnd and hwnd != root and hwnd not in seen:
        seen.add(hwnd)
        if not native.style(hwnd) & WS_VISIBLE:
            return False
        hwnd = native.parent_window(hwnd)
    return hwnd == root


def _parse_amount(raw, field: str) -> float:
    text = (raw or "").strip().replace(",", "")
    if not text:
        raise RuntimeError("资金字段 %s 为空" % field)
    try:
        value = float(text)
    except ValueError:
        raise RuntimeError("资金字段 %s 不是数字: %r" % (field, raw)) from None
    if not math.isfinite(value):
        raise RuntimeError("资金字段 %s 不是有限数字: %r" % (field, raw))
    return value


def _normalise_path(value) -> str:
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _exe_name(value) -> str:
    return os.path.basename(str(value or "")).lower()


def _menu_path(path) -> List[str]:
    if isinstance(path, (str, bytes)):
        raise RuntimeError("菜单路径必须是字符串列表")
    try:
        items = list(path)
    except TypeError:
        raise RuntimeError("菜单路径必须是字符串列表") from None
    if len(items) < 2:
        raise RuntimeError("菜单路径至少需要两级")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError("菜单路径包含无效项")
    return items


class _Native:
    """Win32/DWM 调用的薄封装；构造时不碰任何真实窗口。"""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("同花顺后台模式仅支持 Windows")
        import ctypes
        from ctypes import wintypes

        import win32clipboard
        import win32gui
        import win32process

        self._ctypes = ctypes
        self._wt = wintypes
        self._clipboard = win32clipboard
        self._gui = win32gui
        self._process = win32process
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()

    def _bind(self):
        ctypes = self._ctypes
        wt = self._wt
        self.user.SendMessageTimeoutW.argtypes = [
            wt.HWND,
            wt.UINT,
            wt.WPARAM,
            wt.LPARAM,
            wt.UINT,
            wt.UINT,
            ctypes.POINTER(ctypes.c_ssize_t),
        ]
        self.user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        self.user.PostMessageW.argtypes = [
            wt.HWND,
            wt.UINT,
            wt.WPARAM,
            wt.LPARAM,
        ]
        self.user.PostMessageW.restype = wt.BOOL
        self.dwm.DwmGetWindowAttribute.argtypes = [
            wt.HWND,
            wt.DWORD,
            wt.LPVOID,
            wt.DWORD,
        ]
        self.dwm.DwmGetWindowAttribute.restype = wt.LONG
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wt.HANDLE,
            wt.DWORD,
            wt.LPWSTR,
            ctypes.POINTER(wt.DWORD),
        ]
        self.kernel.QueryFullProcessImageNameW.restype = wt.BOOL
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.restype = wt.BOOL

    def enumerate_top(self) -> List[int]:
        found = []

        def collect(hwnd, _):
            found.append(int(hwnd))
            return True

        self._gui.EnumWindows(collect, None)
        return found

    def children(self, hwnd: int) -> List[int]:
        found = []

        def collect(child, _):
            found.append(int(child))
            return True

        self._gui.EnumChildWindows(int(hwnd), collect, None)
        return found

    def text(self, hwnd: int) -> str:
        return self._gui.GetWindowText(int(hwnd))

    def class_name(self, hwnd: int) -> str:
        return self._gui.GetClassName(int(hwnd))

    def control_id(self, hwnd: int) -> int:
        return int(self._gui.GetDlgCtrlID(int(hwnd)))

    def is_window(self, hwnd: int) -> bool:
        return bool(self._gui.IsWindow(int(hwnd)))

    def is_visible(self, hwnd: int) -> bool:
        return bool(self._gui.IsWindowVisible(int(hwnd)))

    def style(self, hwnd: int) -> int:
        return int(self._gui.GetWindowLong(int(hwnd), GWL_STYLE))

    def ex_style(self, hwnd: int) -> int:
        return int(self._gui.GetWindowLong(int(hwnd), -20))

    def set_ex_style(self, hwnd: int, value: int) -> None:
        self._gui.SetWindowLong(int(hwnd), -20, int(value))

    def owner_window(self, hwnd: int) -> int:
        return int(self._gui.GetWindow(int(hwnd), GW_OWNER) or 0)

    def parent_window(self, hwnd: int) -> int:
        return int(self._gui.GetParent(int(hwnd)) or 0)

    def dlg_item(self, hwnd: int, control_id: int) -> int:
        return int(self._gui.GetDlgItem(int(hwnd), int(control_id)) or 0)

    def foreground_window(self) -> int:
        return int(self._gui.GetForegroundWindow() or 0)

    def process_id(self, hwnd: int) -> int:
        return int(self._process.GetWindowThreadProcessId(int(hwnd))[1])

    def thread_id(self, hwnd: int) -> int:
        return int(self._process.GetWindowThreadProcessId(int(hwnd))[0])

    def process_image(self, pid: int) -> Optional[str]:
        kernel = self.kernel
        handle = kernel.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return None
        try:
            size = self._wt.DWORD(1024)
            buffer = self._ctypes.create_unicode_buffer(size.value)
            if not kernel.QueryFullProcessImageNameW(
                handle, 0, buffer, self._ctypes.byref(size)
            ):
                return None
            return buffer.value
        finally:
            kernel.CloseHandle(handle)

    def cloaked(self, hwnd: int) -> Optional[int]:
        value = self._wt.DWORD()
        hr = self.dwm.DwmGetWindowAttribute(
            int(hwnd),
            DWMWA_CLOAKED,
            self._ctypes.byref(value),
            self._ctypes.sizeof(value),
        )
        if hr != 0:
            return None
        return int(value.value)

    def restore_foreground(self, hwnd):
        if not self.is_window(hwnd):
            return False
        import win32api
        caller = win32api.GetCurrentThreadId()
        foreground = self.foreground_window()
        thread = self.thread_id(foreground) if foreground else 0
        attached = False
        try:
            if thread and thread != caller:
                self._process.AttachThreadInput(caller, thread, True)
                attached = True
            self._gui.SetForegroundWindow(int(hwnd))
        except Exception:
            return False
        finally:
            if attached:
                self._process.AttachThreadInput(caller, thread, False)
        return self.foreground_window() == hwnd

    def hide(self, hwnd):
        self._gui.ShowWindow(int(hwnd), 0)
        if self.is_visible(hwnd):
            raise RuntimeError("窗口未能隐藏: %s" % hwnd)

    def input_desktop_name(self):
        """只读检查 Windows 输入桌面（含锁屏/UAC）；不操作任务视图。"""
        c, wt = self._ctypes, self._wt
        self.user.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.user.OpenInputDesktop.restype = wt.HANDLE
        self.user.GetUserObjectInformationW.argtypes = [wt.HANDLE, c.c_int, wt.LPVOID, wt.DWORD, c.POINTER(wt.DWORD)]
        self.user.GetUserObjectInformationW.restype = wt.BOOL
        self.user.CloseDesktop.argtypes = [wt.HANDLE]
        self.user.CloseDesktop.restype = wt.BOOL
        handle = self.user.OpenInputDesktop(0, False, 1)
        if not handle:
            raise RuntimeError("Windows 输入桌面不可访问，请先解除锁屏或安全提示")
        try:
            name = c.create_unicode_buffer(256)
            needed = wt.DWORD()
            if not self.user.GetUserObjectInformationW(handle, 2, name, c.sizeof(name), c.byref(needed)):
                raise RuntimeError("无法读取 Windows 输入桌面名称")
            return name.value
        finally:
            self.user.CloseDesktop(handle)

    def set_visible_no_activate(self, hwnd: int) -> bool:
        self._gui.SetWindowPos(int(hwnd), 0, 0, 0, 0, 0, SWP_NO_ACTIVATE_SHOW)
        return bool(self._gui.IsWindowVisible(int(hwnd)))

    def send_timeout(self, hwnd: int, message: int, wparam=0, lparam=0):
        ctypes = self._ctypes
        result = ctypes.c_ssize_t()
        ctypes.set_last_error(0)
        ok = self.user.SendMessageTimeoutW(
            int(hwnd),
            int(message),
            int(wparam),
            int(lparam),
            SMTO_ABORTIFHUNG,
            SEND_TIMEOUT_MS,
            ctypes.byref(result),
        )
        if not ok:
            code = ctypes.get_last_error()
            if code:
                detail = ctypes.FormatError(code)
            else:
                detail = "控件未响应"
            raise RuntimeError(
                "控件消息 0x%04X 失败: %s" % (int(message), detail)
            )
        return int(result.value)

    def post_message(self, hwnd: int, message: int, wparam=0, lparam=0):
        self._ctypes.set_last_error(0)
        ok = self.user.PostMessageW(
            int(hwnd), int(message), int(wparam), int(lparam)
        )
        return bool(ok)

    def clipboard_sequence(self) -> int:
        return int(self._clipboard.GetClipboardSequenceNumber())

    def clipboard_text(self) -> Optional[str]:
        try:
            self._clipboard.OpenClipboard()
        except Exception:
            return None
        value = None
        try:
            if self._clipboard.IsClipboardFormatAvailable(
                self._clipboard.CF_UNICODETEXT
            ):
                value = self._clipboard.GetClipboardData(
                    self._clipboard.CF_UNICODETEXT
                )
        except Exception:
            value = None
        finally:
            try:
                self._clipboard.CloseClipboard()
            except Exception:
                pass
        return value if isinstance(value, str) else None

    def clipboard_owner(self) -> int:
        return int(self._clipboard.GetClipboardOwner() or 0)

    def tree_item(self, hwnd: int, path: List[str]) -> int:
        # get_item 只读取项目句柄；选择一律走 TVM_SELECTITEM。
        from pywinauto.controls.common_controls import TreeViewWrapper

        wrapper = TreeViewWrapper(int(hwnd))
        item = wrapper.get_item(list(path))
        if item is None:
            raise RuntimeError("菜单项不存在")
        return int(item.elem)


class WindowsBackend:
    """远航版只读后台通道。

    ``WindowsBackend()`` 只保存状态，不导入 Win32，也不碰窗口；
    第一次真正用到时才建立原生绑定。
    """

    def __init__(self, windows=None):
        self._native_api = windows
        self.owner_hwnd = None
        self.trader_hwnd = None
        self.owner_pid = None
        self.trader_pid = None
        self._discovered = False
        self._tree_hwnd = 0
        self._original_caret = 0
        self._original_visible = True
        self._original_trader_visible = False
        self._hidden_active = False
        self._query_captcha_allowed = False
        self._captcha_input_active = False
        self._input_captcha = None
        self._return_foreground = 0
        self._activation_styles = {}

    # ------------------------------------------------------------------
    # 依赖与状态
    # ------------------------------------------------------------------
    def _native(self):
        if self._native_api is None:
            self._native_api = _Native()
        return self._native_api

    def _require_discovered(self) -> None:
        if not self._discovered:
            raise RuntimeError("请先调用 discover() 连接已登录的同花顺远航版")

    # ------------------------------------------------------------------
    # 连接前检查
    # ------------------------------------------------------------------
    def discover(self, exe_path=None) -> None:
        """绑定唯一一组远航版/下单窗口并记录原始状态。"""
        native = self._native()
        if self._hidden_active:
            raise RuntimeError("隐藏查询会话仍在使用，请先关闭或恢复窗口")

        owners = []
        traders = []
        for hwnd in native.enumerate_top():
            try:
                title = native.text(hwnd)
            except Exception:
                continue
            if title == VOYAGER_TITLE:
                owners.append(hwnd)
            elif title.startswith(TRADER_TITLE_PREFIX):
                traders.append(hwnd)
        if len(owners) != 1:
            raise RuntimeError(
                "需要且仅需要一个“%s”窗口，实际 %d 个"
                % (VOYAGER_TITLE, len(owners))
            )
        if len(traders) != 1:
            raise RuntimeError(
                "需要且仅需要一个“%s”下单窗口，实际 %d 个"
                % (TRADER_TITLE_PREFIX, len(traders))
            )
        owner, trader = owners[0], traders[0]
        owner_pid = native.process_id(owner)
        trader_pid = native.process_id(trader)
        if native.owner_window(trader) != owner:
            raise RuntimeError("下单窗口不属于该远航版窗口")

        owner_exe = native.process_image(owner_pid)
        trader_exe = native.process_image(trader_pid)
        if _exe_name(owner_exe) != "happ.exe":
            raise RuntimeError("远航版进程不是 happ.exe: %s" % owner_exe)
        if _exe_name(trader_exe) != "xiadan.exe":
            raise RuntimeError("下单进程不是 xiadan.exe: %s" % trader_exe)
        if exe_path and _normalise_path(trader_exe) != _normalise_path(
            exe_path
        ):
            raise RuntimeError(
                "下单程序路径不匹配: %s != %s" % (trader_exe, exe_path)
            )

        trees = [
            child
            for child in native.children(trader)
            if native.class_name(child) == QUERY_TREE_CLASS
            and native.control_id(child) == QUERY_TREE_ID
        ]
        if len(trees) != 1:
            raise RuntimeError(
                "查询菜单尚未就绪（%s#%d 数量 %d）"
                % (QUERY_TREE_CLASS, QUERY_TREE_ID, len(trees))
            )
        tree = trees[0]
        caret = self._caret(native, tree)
        self.owner_hwnd = owner
        self.trader_hwnd = trader
        self.owner_pid = owner_pid
        self.trader_pid = trader_pid
        self._tree_hwnd = tree
        self._original_caret = caret
        self._original_visible = bool(native.is_visible(owner))
        self._original_trader_visible = bool(native.is_visible(trader))
        self._discovered = True
        return None

    def current_desktop_id(self) -> str:
        return self._native().input_desktop_name()

    def foreground_pid(self) -> int:
        native = self._native()
        hwnd = native.foreground_window()
        if not hwnd:
            return 0
        try:
            return int(native.process_id(hwnd))
        except Exception:
            return 0

    def identity_ok(self) -> bool:
        try:
            native = self._native()
        except RuntimeError:
            return False
        if not self.owner_hwnd or not self.trader_hwnd:
            return False
        return self._matches(
            native, self.owner_hwnd, self.owner_pid
        ) and self._matches(native, self.trader_hwnd, self.trader_pid)

    @staticmethod
    def _matches(native, hwnd: int, pid: int) -> bool:
        try:
            if not native.is_window(hwnd):
                return False
            return int(native.process_id(hwnd)) == int(pid)
        except Exception:
            return False

    def enter_background(self):
        """只隐藏当前客户端窗口；不操作任务视图桌面。"""
        self._require_discovered()
        if self._hidden_active:
            raise RuntimeError("隐藏查询会话已经开始")
        if not self.identity_ok():
            raise RuntimeError("客户端窗口已改变")
        native = self._native()
        # 先隐藏附属窗口，再隐藏 owner，避免 owner 隐藏期间残留子窗口。
        native.hide(self.trader_hwnd)
        native.hide(self.owner_hwnd)
        self._hidden_active = True
        self.assert_background()

    def restore(self):
        """显式恢复连接前可见性。调用者主动要求显示时才使用。"""
        if not self._discovered or not self._hidden_active:
            return
        if self.captcha():
            raise RuntimeError("仍有查询验证码，未自动显示主窗口")
        errors = self._restore_caret(self._native())
        if errors:
            raise RuntimeError("；".join(errors))
        native = self._native()
        for hwnd, visible, pid in (
            (self.owner_hwnd, self._original_visible, self.owner_pid),
            (self.trader_hwnd, self._original_trader_visible, self.trader_pid),
        ):
            if self._matches(native, hwnd, pid):
                if visible:
                    native.set_visible_no_activate(hwnd)
                else:
                    native.hide(hwnd)
        self._hidden_active = False

    def allow_query_captcha(self, allowed):
        self._query_captcha_allowed = bool(allowed)

    def begin_captcha_input(self, controls):
        if not self._query_captcha_allowed or not self.check_captcha(controls):
            raise RuntimeError("验证码输入未授权")
        self._captcha_input_active = True
        self._input_captcha = controls

    def foreground_permitted(self, foreground=None):
        """验证码及关闭后隐藏 owner 的短暂过渡，均不允许主窗口显示。"""
        if not self._query_captcha_allowed:
            return False
        native = self._native()
        if foreground is None:
            foreground = native.foreground_window()
        cap = self.captcha()
        if cap and self.check_captcha(cap):
            if foreground in (cap.dialog, cap.editor, cap.ok, cap.cancel, cap.image):
                return True
        cap = self._input_captcha
        if self._captcha_input_active and cap and self.check_captcha(cap):
            if foreground in (cap.dialog, cap.editor, cap.ok, cap.cancel, cap.image):
                return True
        return bool(
            self._captcha_input_active
            and foreground in (self.owner_hwnd, self.trader_hwnd)
            and not native.is_visible(foreground)
        )

    def assert_query_foreground(self):
        native = self._native()
        foreground = native.foreground_window()
        if not foreground:
            return
        try:
            pid = native.process_id(foreground)
        except Exception:
            return  # foreground was destroyed between the two Win32 calls
        if pid in (self.owner_pid, self.trader_pid) and not self.foreground_permitted(foreground):
            raise RuntimeError("非验证码客户端窗口占用了前台（hwnd=%s）" % foreground)


    def select_page(self, path) -> None:
        items = _menu_path(path)
        self._require_discovered()
        native = self._native()
        tree = self._tree(native)
        try:
            target = native.tree_item(tree, items)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "无法定位菜单项 %s: %s" % ("/".join(items), exc)
            ) from exc
        if self._caret(native, tree) == target:
            return None
        self._select_item(native, tree, target)
        return None

    def _tree(self, native) -> int:
        tree = self._tree_hwnd
        if not tree or not native.is_window(tree):
            raise RuntimeError("查询菜单已失效，请重新 discover()")
        if native.class_name(tree) != QUERY_TREE_CLASS:
            raise RuntimeError("查询菜单控件类已改变")
        if native.control_id(tree) != QUERY_TREE_ID:
            raise RuntimeError("查询菜单控件 ID 已改变")
        return tree

    def _caret(self, native, tree: int) -> int:
        try:
            return native.send_timeout(tree, TVM_GETNEXTITEM, TVGN_CARET, 0)
        except RuntimeError as exc:
            raise RuntimeError("读取菜单选中项失败: %s" % exc) from exc

    def _select_item(self, native, tree: int, item: int) -> None:
        try:
            accepted = native.send_timeout(
                tree, TVM_SELECTITEM, TVGN_CARET, item
            )
        except RuntimeError as exc:
            raise RuntimeError("切换菜单失败: %s" % exc) from exc
        if not accepted:
            raise RuntimeError("菜单未接受后台切换")
        if self._caret(native, tree) != item:
            raise RuntimeError("菜单切换后未生效")

    def _restore_caret(self, native) -> List[str]:
        caret = self._original_caret
        tree = self._tree_hwnd
        if not caret or not tree or not native.is_window(tree):
            return []
        if native.class_name(tree) != QUERY_TREE_CLASS:
            return []
        try:
            if self._caret(native, tree) == caret:
                return []
            self._select_item(native, tree, caret)
        except Exception as exc:
            return ["恢复查询菜单失败: %s" % exc]
        return []

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------
    def _live_trader(self, native) -> int:
        hwnd = self.trader_hwnd
        if not hwnd or not native.is_window(hwnd):
            raise RuntimeError("下单窗口已关闭，请重新 discover()")
        if native.process_id(hwnd) != self.trader_pid:
            raise RuntimeError("下单窗口已被其他进程占用")
        return hwnd

    def balance(self) -> Dict[str, float]:
        self._require_discovered()
        native = self._native()
        root = self._live_trader(native)
        result = {}
        for name, control_id in CommonConfig.BALANCE_CONTROL_ID_GROUP.items():
            matches = [
                child
                for child in native.children(root)
                if native.class_name(child) == BALANCE_CLASS
                and native.control_id(child) == control_id
                and _layout_visible(native, child, root)
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "资金字段 %s 的可见控件数为 %d" % (name, len(matches))
                )
            result[name] = _parse_amount(native.text(matches[0]), name)
        return result

    def grid(self) -> int:
        self._require_discovered()
        native = self._native()
        root = self._live_trader(native)
        control_id = CommonConfig.COMMON_GRID_CONTROL_ID
        matches = [
            child
            for child in native.children(root)
            if native.class_name(child) == GRID_CLASS
            and native.control_id(child) == control_id
            and _layout_visible(native, child, root)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "当前查询页的表格数为 %d，拒绝复制" % len(matches)
            )
        return matches[0]

    def copy_grid(self, hwnd: int) -> None:
        self._require_discovered()
        native = self._native()
        handle = _as_handle(hwnd)
        if not native.is_window(handle):
            raise RuntimeError("表格控件已失效")
        if native.process_id(handle) != self.trader_pid:
            raise RuntimeError("表格控件不属于下单窗口")
        if native.class_name(handle) != GRID_CLASS:
            raise RuntimeError("表格控件类不是 %s" % GRID_CLASS)
        if not native.post_message(handle, WM_COMMAND, GRID_COPY_COMMAND, 0):
            raise RuntimeError("发送表格复制命令失败")
        return None

    # ------------------------------------------------------------------
    # 剪贴板（只读）
    # ------------------------------------------------------------------
    def clipboard_sequence(self) -> int:
        return self._native().clipboard_sequence()

    def clipboard_text(self) -> Optional[str]:
        return self._native().clipboard_text()

    def clipboard_owner_pid(self) -> Optional[int]:
        native = self._native()
        owner = native.clipboard_owner()
        if not owner:
            return None
        try:
            return int(native.process_id(owner))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 验证码
    # ------------------------------------------------------------------
    def captcha(self) -> Optional[CaptchaControls]:
        self._require_discovered()
        native = self._native()
        found = []
        for hwnd in native.enumerate_top():
            try:
                if native.process_id(hwnd) != self.trader_pid:
                    continue
                if native.class_name(hwnd) != CAPTCHA_DIALOG_CLASS:
                    continue
                if not native.is_visible(hwnd):
                    continue
                image = native.dlg_item(hwnd, CAPTCHA_IMAGE_ID)
                editor = native.dlg_item(hwnd, CAPTCHA_EDITOR_ID)
            except Exception:
                continue
            if not image or not editor:
                continue
            ok = native.dlg_item(hwnd, CAPTCHA_OK_ID)
            cancel = native.dlg_item(hwnd, CAPTCHA_CANCEL_ID)
            if not ok or not cancel:
                raise RuntimeError("验证码对话框缺少确定/取消按钮")
            found.append(CaptchaControls(hwnd, image, editor, ok, cancel))
        if len(found) > 1:
            raise RuntimeError("发现多个验证码对话框，拒绝自动处理")
        return found[0] if found else None

    def check_captcha(self, caps: CaptchaControls) -> bool:
        if not isinstance(caps, CaptchaControls):
            raise RuntimeError("check_captcha 需要 CaptchaControls")
        self._require_discovered()
        native = self._native()
        if not native.is_window(caps.dialog):
            return False
        if native.process_id(caps.dialog) != self.trader_pid:
            return False
        if native.class_name(caps.dialog) != CAPTCHA_DIALOG_CLASS:
            return False
        expected = (
            (caps.image, CAPTCHA_IMAGE_ID),
            (caps.editor, CAPTCHA_EDITOR_ID),
            (caps.ok, CAPTCHA_OK_ID),
            (caps.cancel, CAPTCHA_CANCEL_ID),
        )
        for hwnd, control_id in expected:
            if not native.is_window(hwnd):
                return False
            if native.parent_window(hwnd) != caps.dialog:
                return False
            if native.control_id(hwnd) != control_id:
                return False
            if native.process_id(hwnd) != self.trader_pid:
                return False
        return True

    def _protect_window(self, hwnd, pid):
        native = self._native()
        if not self._matches(native, hwnd, pid):
            raise RuntimeError("窗口身份已改变，未设置防激活样式")
        if hwnd not in self._activation_styles:
            original = native.ex_style(hwnd)
            self._activation_styles[hwnd] = (pid, bool(original & 0x08000000))
            if not original & 0x08000000:
                native.set_ex_style(hwnd, original | 0x08000000)

    def validate_activation_guard(self):
        from easytrader.background_focus import pe_machine

        if not self.identity_ok():
            raise RuntimeError("下单窗口身份已改变")
        if pe_machine(self._native().process_image(self.trader_pid)) != 0x14C:
            raise RuntimeError("当前线程激活保护仅验收了 x86 同花顺客户端")

    def hook_target(self):
        self.validate_activation_guard()
        native = self._native()
        thread = native.thread_id(self.trader_hwnd)
        if (
            not thread
            or native.process_id(self.trader_hwnd) != self.trader_pid
        ):
            raise RuntimeError("下单窗口线程身份已改变")
        return self.trader_hwnd, self.trader_pid, thread

    def assert_background(self):
        """主窗口必须隐藏；只允许本次查询的验证码临时可见。"""
        if not self.identity_ok() or not self._hidden_active:
            raise RuntimeError("隐藏查询会话或窗口身份已失效")
        native = self._native()
        cap = self.captcha() if self._query_captcha_allowed else None
        permitted = cap.dialog if cap and self.check_captcha(cap) else None
        for hwnd in native.enumerate_top():
            try:
                if native.process_id(hwnd) not in (self.owner_pid, self.trader_pid):
                    continue
                if native.is_visible(hwnd) and hwnd != permitted:
                    raise RuntimeError("客户端出现了非验证码可见窗口，查询已停止")
            except RuntimeError:
                raise
            except Exception:
                if native.is_window(hwnd):
                    raise RuntimeError("无法确认客户端窗口可见性")

    def ensure_render_ready(self):
        # 隐藏状态直接依靠控件消息与内存绘图，绝不自动显示主窗口。
        self.assert_background()

    def begin_read(self):
        self.assert_background()
        self._captcha_input_active = False
        foreground = self._native().foreground_window()
        if foreground and self.foreground_pid() not in (self.owner_pid, self.trader_pid):
            self._return_foreground = foreground

    def protect_captcha(self, controls):
        if not self._query_captcha_allowed or not self.check_captcha(controls):
            raise RuntimeError("不是本次查询已允许的验证码")

    def end_read(self):
        # 收起主窗口。未识别的弹窗只报告失败，不擅自确认或关闭。
        native = self._native()
        for hwnd, pid in ((self.trader_hwnd, self.trader_pid), (self.owner_hwnd, self.owner_pid)):
            if self._matches(native, hwnd, pid):
                native.hide(hwnd)
        self._query_captcha_allowed = False
        if self._captcha_input_active and self.foreground_pid() in (self.owner_pid, self.trader_pid):
            if not self._return_foreground or not native.restore_foreground(self._return_foreground):
                raise RuntimeError("查询完成但未能归还前台焦点，请手动切回工作窗口")
        self._captcha_input_active = False
        self._input_captcha = None

    def capture_captcha(self, image_hwnd: int):
        self._require_discovered()
        native = self._native()
        handle = _as_handle(image_hwnd)
        if not native.is_window(handle):
            raise RuntimeError("验证码图片控件已失效")
        if native.process_id(handle) != self.trader_pid:
            raise RuntimeError("验证码图片不属于下单窗口")
        if native.control_id(handle) != CAPTCHA_IMAGE_ID:
            raise RuntimeError("验证码图片控件 ID 已改变")
        from easytrader.screenshot import verified_control_image

        return verified_control_image(handle)

    def cancel_captcha(self, caps: CaptchaControls) -> None:
        """Cancel only the known query captcha by notification."""
        if not self.check_captcha(caps):
            raise RuntimeError("验证码控件已改变，未发送取消通知")
        if not self._native().post_message(caps.cancel, 0x00F5):
            raise RuntimeError("发送查询验证码取消失败")
