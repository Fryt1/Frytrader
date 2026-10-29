# -*- coding: utf-8 -*-
"""同花顺远航版只读后台通道（任务视图虚拟桌面 + 定向控件消息）。

边界
----
- 只操作已经登录运行的远航版：启动、关闭、登录都归用户。
- 只发控件消息（SendMessageTimeoutW / PostMessageW），
  不调用 SendInput、不抢焦点、不模拟键鼠、不调用 SwitchDesktop。
- 任务视图虚拟桌面不是独立键盘环境。本模块只把远航版窗口
  移到自己新建的桌面，用户桌面保持不变；恢复时只动自己创建的桌面。
- 剪贴板只读取序号、文本和属主，不清空、不写入、不还原。
- 依赖按需导入，非 Windows 上 import 本模块不会失败；
  pyvda 是可选依赖，只有真正读写桌面时才需要。

调用约定
--------
``WindowsBackend`` 的公开方法在无法安全继续时抛出 ``RuntimeError``。
``restore()`` 在从未 ``discover()`` 或从未进入后台时是幂等的空操作；
真正的清理失败会抛出 ``RuntimeError``，不会静默报告成功。
"""

import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from easytrader.config.client import CommonConfig

VOYAGER_TITLE = "同花顺远航版"
TRADER_TITLE_PREFIX = "网上股票交易系统5.0"
DEFAULT_DESKTOP_NAME = "Frytrader 后台"

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

# 移动窗口后等待附属下单窗口跟随的边界。
FOLLOW_TIMEOUT_S = 1.5
FOLLOW_POLL_S = 0.02


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


def _desktop_name(value) -> str:
    if not isinstance(value, str):
        raise RuntimeError("desktop_name 必须是字符串")
    name = value.strip()
    if not name:
        raise RuntimeError("desktop_name 不能为空")
    if len(name) > 60:
        raise RuntimeError("desktop_name 不能超过 60 个字符")
    if "\x00" in name:
        raise RuntimeError("desktop_name 不能包含空字符")
    return name


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


class _Desktops:
    """pyvda 的薄封装；每次调用都重新解析，避免跨线程 COM 指针。"""

    def __init__(self):
        try:
            import pyvda
        except Exception as exc:  # pragma: no cover - 环境缺依赖
            raise RuntimeError("任务视图后台模式需要 pyvda: %s" % exc) from exc
        self._pyvda = pyvda

    def current_id(self) -> str:
        return str(self._pyvda.VirtualDesktop.current().id)

    def create(self, name: str) -> str:
        desktop = self._pyvda.VirtualDesktop.create()
        desktop_id = str(desktop.id)
        try:
            try:
                desktop.rename(name)
            except NotImplementedError:
                pass
        except BaseException:
            current = self._pyvda.VirtualDesktop.current()
            if str(current.id) != desktop_id and not desktop.apps_by_z_order(
                include_pinned=False
            ):
                desktop.remove(fallback=current)
            raise
        return desktop_id

    def _find(self, desktop_id: str):
        for desktop in self._pyvda.get_virtual_desktops():
            if str(desktop.id) == desktop_id:
                return desktop
        return None

    def exists(self, desktop_id: str) -> bool:
        return self._find(desktop_id) is not None

    def view_desktop_id(self, hwnd: int) -> Optional[str]:
        return str(self._pyvda.AppView(int(hwnd)).desktop_id)

    def move(self, hwnd: int, desktop_id: str) -> None:
        desktop = self._find(desktop_id)
        if desktop is None:
            raise RuntimeError("目标桌面已不存在")
        self._pyvda.AppView(int(hwnd)).move(desktop)

    def is_pinned(self, hwnd: int) -> bool:
        view = self._pyvda.AppView(int(hwnd))
        return bool(view.is_pinned()) or bool(view.is_app_pinned())

    def apps(self, desktop_id: str) -> List[int]:
        desktop = self._find(desktop_id)
        if desktop is None:
            return []
        views = desktop.apps_by_z_order(include_pinned=False)
        return [int(view.hwnd) for view in views]

    def remove(self, desktop_id: str) -> bool:
        desktop = self._find(desktop_id)
        if desktop is None:
            return False
        current = self._pyvda.VirtualDesktop.current()
        if str(current.id) == desktop_id:
            # 删除当前桌面会让 Windows 切换桌面，这里直接拒绝。
            raise RuntimeError("不能删除当前活动桌面")
        desktop.remove(fallback=current)
        return True


class WindowsBackend:
    """远航版只读后台通道。

    ``WindowsBackend()`` 只保存状态，不导入 Win32/pyvda，也不碰窗口；
    第一次真正用到时才建立原生绑定。
    """

    def __init__(self, windows=None, desktops=None):
        self._native_api = windows
        self._desktop_api = desktops
        self.owner_hwnd = None
        self.trader_hwnd = None
        self.owner_pid = None
        self.trader_pid = None
        self._discovered = False
        self._tree_hwnd = 0
        self._original_caret = 0
        self._original_visible = True
        self._original_trader_visible = False
        self._original_desktop_id = None
        self._target_desktop_id = None
        self._source_desktop_id = None
        self._borrowed_desktop = False
        self._working_desktop_id = None
        self._activation_styles = {}

    # ------------------------------------------------------------------
    # 依赖与状态
    # ------------------------------------------------------------------
    def _native(self):
        if self._native_api is None:
            self._native_api = _Native()
        return self._native_api

    def _desktops(self):
        if self._desktop_api is None:
            self._desktop_api = _Desktops()
        return self._desktop_api

    def _require_discovered(self) -> None:
        if not self._discovered:
            raise RuntimeError("请先调用 discover() 连接已登录的同花顺远航版")

    @property
    def target_desktop_id(self) -> Optional[str]:
        return self._target_desktop_id

    @property
    def source_desktop_id(self) -> Optional[str]:
        return self._source_desktop_id

    # ------------------------------------------------------------------
    # 连接前检查
    # ------------------------------------------------------------------
    def discover(self, exe_path=None) -> None:
        """绑定唯一一组远航版/下单窗口并记录原始状态。"""
        native = self._native()
        if self._target_desktop_id is not None:
            raise RuntimeError("后台桌面尚未恢复，请先调用 restore()")

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
        try:
            original_desktop = self._desktops().view_desktop_id(owner)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "读取远航版所在任务视图桌面失败: %s" % exc
            ) from exc

        self.owner_hwnd = owner
        self.trader_hwnd = trader
        self.owner_pid = owner_pid
        self.trader_pid = trader_pid
        self._tree_hwnd = tree
        self._original_caret = caret
        self._original_visible = bool(native.is_visible(owner))
        self._original_trader_visible = bool(native.is_visible(trader))
        self._original_desktop_id = original_desktop
        self._discovered = True
        return None

    def current_desktop_id(self) -> str:
        return self._desktops().current_id()

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

    # ------------------------------------------------------------------
    # 进入/退出后台桌面
    # ------------------------------------------------------------------
    def enter_background(self, desktop_name=DEFAULT_DESKTOP_NAME) -> None:
        """只移动远航版窗口；归属的下单窗口会跟随。"""
        self._require_discovered()
        if self._target_desktop_id is not None:
            raise RuntimeError("后台桌面已经进入，请先调用 restore()")
        name = _desktop_name(desktop_name)
        native = self._native()
        desktops = self._desktops()
        owner = self.owner_hwnd
        if not native.is_window(owner):
            raise RuntimeError("远航版窗口已关闭，请重新 discover()")
        if native.process_id(owner) != self.owner_pid:
            raise RuntimeError("远航版窗口已被其他进程占用")
        try:
            source = desktops.current_id()
            owner_desktop = desktops.view_desktop_id(owner)
            pinned = desktops.is_pinned(owner)
        except RuntimeError as exc:
            raise RuntimeError("读取任务视图桌面失败: %s" % exc) from exc
        except Exception as exc:
            raise RuntimeError("读取任务视图桌面失败: %s" % exc) from exc
        if pinned:
            raise RuntimeError("远航版已固定到所有桌面，未进入后台")
        self._working_desktop_id = source
        if owner_desktop != source:
            if not native.cloaked(self.trader_hwnd):
                raise RuntimeError("下单窗口未随远航版留在另一桌面")
            self._borrowed_desktop = True
            self._target_desktop_id = owner_desktop
            self._source_desktop_id = owner_desktop
            return None
        self._borrowed_desktop = False
        try:
            target = desktops.create(name)
        except Exception as exc:
            raise RuntimeError("创建后台桌面失败: %s" % exc) from exc
        self._target_desktop_id = target
        self._source_desktop_id = source
        try:
            desktops.move(owner, target)
            self._wait_follow(native, desktops, target)
        except BaseException as exc:
            problems = self._revert_entry(native, desktops)
            if isinstance(exc, Exception):
                message = "进入后台桌面失败: %s" % exc
                if problems:
                    message += "；回滚未完成: " + "；".join(problems)
                raise RuntimeError(message) from exc
            raise
        return None

    def _wait_follow(self, native, desktops, target: str) -> None:
        deadline = time.monotonic() + FOLLOW_TIMEOUT_S
        last_cloak = None
        while True:
            if desktops.current_id() != self._source_desktop_id:
                raise RuntimeError("用户桌面在移动期间发生变化，已停止")
            if not native.is_window(self.trader_hwnd):
                raise RuntimeError("下单窗口在移动期间消失")
            if native.process_id(self.trader_hwnd) != self.trader_pid:
                raise RuntimeError("下单窗口在移动期间被其他进程占用")
            last_cloak = native.cloaked(self.trader_hwnd)
            try:
                followed = desktops.view_desktop_id(self.owner_hwnd) == (
                    target
                )
            except Exception:
                followed = False
            if last_cloak and followed:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "下单窗口未随远航版进入后台桌面（DWM cloak=%s）"
                    % last_cloak
                )
            time.sleep(FOLLOW_POLL_S)

    def restore(self) -> None:
        """只恢复自己移动的窗口；未进入后台时不操作客户端。"""
        if not self._discovered or self._target_desktop_id is None:
            return None
        if self._borrowed_desktop:
            self._target_desktop_id = None
            self._source_desktop_id = None
            self._borrowed_desktop = False
            return None
        native = self._native()
        desktops = self._desktops()
        problems = []
        ours = False
        try:
            ours = self._matches(native, self.owner_hwnd, self.owner_pid) and (
                desktops.view_desktop_id(self.owner_hwnd)
                == self._target_desktop_id
            )
        except Exception as exc:
            problems.append("无法确认窗口桌面归属: %s" % exc)
        if ours:
            problems.extend(self._restore_caret(native))
            problems.extend(self._restore_owner_desktop(native, desktops))
            problems.extend(self._restore_visibility(native))
        problems.extend(self._remove_target(native, desktops))
        if self._target_desktop_id is None:
            self._source_desktop_id = None
        if problems:
            raise RuntimeError("；".join(problems))
        return None

    def _revert_entry(self, native, desktops) -> List[str]:
        problems = []
        target = self._target_desktop_id
        if target is None:
            return problems
        owner = self.owner_hwnd
        try:
            if native.is_window(owner) and (
                desktops.view_desktop_id(owner) == target
            ):
                desktops.move(owner, self._source_desktop_id)
        except Exception as exc:
            problems.append("恢复远航版窗口失败: %s" % exc)
        problems.extend(self._remove_target(native, desktops))
        if self._target_desktop_id is None:
            self._source_desktop_id = None
        return problems

    def _restore_owner_desktop(self, native, desktops) -> List[str]:
        target = self._target_desktop_id
        if target is None:
            return []
        owner = self.owner_hwnd
        if not self._matches(native, owner, self.owner_pid):
            return []
        try:
            current = desktops.view_desktop_id(owner)
        except Exception as exc:
            return ["无法读取远航版所在桌面: %s" % exc]
        if current != target:
            # 用户已自行移动，保持现状。
            return []
        try:
            desktops.move(owner, self._source_desktop_id)
        except Exception as exc:
            return ["远航版未能移回原桌面: %s" % exc]
        return []

    def _remove_target(self, native, desktops) -> List[str]:
        target = self._target_desktop_id
        if target is None:
            return []
        try:
            if not desktops.exists(target):
                self._target_desktop_id = None
                return []
            if desktops.current_id() == target:
                return ["后台桌面仍处于活动状态，已保留未删除"]
            leftover = desktops.apps(target)
            if leftover:
                problems = []
                if self._has_our_window(native, leftover):
                    problems.append("远航版或其下单窗口仍留在后台桌面")
                return problems
            desktops.remove(target)
        except Exception as exc:
            return ["删除后台桌面失败: %s" % exc]
        self._target_desktop_id = None
        return []

    def _has_our_window(self, native, handles) -> bool:
        ours = {self.owner_pid, self.trader_pid}
        for hwnd in handles:
            try:
                if native.process_id(hwnd) in ours:
                    return True
            except Exception:
                continue
        return False

    def _restore_visibility(self, native) -> List[str]:
        problems = []
        for hwnd, pid, was_visible in (
            (self.owner_hwnd, self.owner_pid, self._original_visible),
            (self.trader_hwnd, self.trader_pid, self._original_trader_visible),
        ):
            if not was_visible or not self._matches(native, hwnd, pid):
                continue
            if native.is_visible(hwnd):
                continue
            try:
                if not native.set_visible_no_activate(hwnd):
                    problems.append("恢复窗口显示状态失败")
            except Exception as exc:
                problems.append("恢复窗口显示状态失败: %s" % exc)
        return problems

    # ------------------------------------------------------------------
    # 查询菜单
    # ------------------------------------------------------------------
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
                if not native.is_visible(hwnd) and not native.cloaked(hwnd):
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
        """Read-only check; never try to fix placement after interference."""
        if not self.identity_ok() or self._target_desktop_id is None:
            raise RuntimeError("后台会话或窗口身份已失效")
        desktops = self._desktops()
        if desktops.current_id() == self._target_desktop_id:
            raise RuntimeError("客户端桌面已成为当前桌面")
        if (
            desktops.view_desktop_id(self.owner_hwnd)
            != self._target_desktop_id
        ):
            raise RuntimeError("远航版已离开后台桌面")
        native = self._native()
        pids = {self.owner_pid, self.trader_pid}
        for hwnd in native.enumerate_top():
            try:
                if native.process_id(hwnd) not in pids:
                    continue
                if not native.is_visible(hwnd):
                    continue
                cloaked = native.cloaked(hwnd)
            except Exception:
                if native.is_window(hwnd):
                    raise RuntimeError("无法确认客户端窗口是否被遮蔽")
                continue
            if not cloaked:
                raise RuntimeError(
                    "客户端有可见且未遮蔽的窗口，后台操作已停止"
                )

    def ensure_render_ready(self):
        """Show only the cloaked trader, with NOACTIVATE + CBT active."""
        native = self._native()
        if not self.identity_ok() or not native.cloaked(self.trader_hwnd):
            raise RuntimeError("下单窗口未留在后台桌面")
        if not native.is_visible(self.trader_hwnd):
            if not native.set_visible_no_activate(self.trader_hwnd):
                raise RuntimeError("后台下单窗口的可绘图状态未就绪")

    def begin_read(self):
        self._protect_window(self.owner_hwnd, self.owner_pid)
        self._protect_window(self.trader_hwnd, self.trader_pid)

    def protect_captcha(self, controls):
        if not self.check_captcha(controls):
            raise RuntimeError("验证码窗口身份已改变")
        self._protect_window(controls.dialog, self.trader_pid)

    def end_read(self):
        native = self._native()
        errors = []
        for hwnd, (pid, originally_set) in list(
            self._activation_styles.items()
        ):
            if not self._matches(native, hwnd, pid):
                continue
            try:
                current = native.ex_style(hwnd)
                restored = (
                    current | 0x08000000
                    if originally_set
                    else current & ~0x08000000
                )
                if restored != current:
                    native.set_ex_style(hwnd, restored)
            except Exception as exc:
                errors.append(str(exc))
        self._activation_styles.clear()
        if errors:
            raise RuntimeError("恢复防激活样式失败: " + "; ".join(errors))

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
        self._native().send_timeout(
            caps.dialog, WM_COMMAND, CAPTCHA_CANCEL_ID, caps.cancel
        )
