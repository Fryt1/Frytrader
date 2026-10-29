# -*- coding: utf-8 -*-
"""窗口截图：用 PrintWindow 在内存里取图，不依赖屏幕，也不改变窗口状态。

适用场景
--------
同花顺 Voyager 客户端被放在 Windows「任务视图」的另一个虚拟桌面上，
由 AutoHotkey 按控件定向发送消息（ControlSendText / ControlClick）操作。
这时验证码图片抓不到：窗口不在用户当前虚拟桌面上，或窗口被遮挡、
位于屏幕外，ImageGrab / pywinauto 的 capture_as_image 读的是屏幕像素，
会抛 "screen grab failed"。

``PrintWindow`` 让目标窗口把自己画到调用方提供的内存 DC 上，不读屏幕，
因此与窗口是否在前台、是否在当前虚拟桌面无关。已知前提：调用方与窗口
在同一会话、同一完整性级别；UIPI 或跨会话会让 PrintWindow 返回失败，
这时本模块抛 CaptureError，由调用方决定失败策略。

本模块的约束
------------
- **抓图路径只读**：``print_window`` 只调用 GetWindowRect / GetWindowDC /
  PrintWindow 和 GDI 画图接口，不调用 SetForegroundWindow、ShowWindow、
  SetWindowPos 等会改变窗口状态或抢焦点的函数；后台线程调用它不会把用户
  从当前窗口或当前虚拟桌面切走，也不会让目标窗口闪现。
- **不落盘**：像素从内存位图直接交给 Pillow，不写临时 BMP，
  不存在固定文件名互相覆盖或残留文件的问题。
- **资源必还**：窗口 DC、内存 DC、位图、被替换的旧 GDI 对象在 ``finally``
  里按依赖顺序释放；失败路径与成功路径的清理行为一致。

与 ensure_rendered 的关系
-------------------------
``ensure_rendered`` 会调用 ``ShowWindow``，那是**改变窗口显示状态**的动作，
仅用于兼容已有调用方（grid_strategies 的前台抓图回退路径）。
后台 / 跨桌面场景不要调用它；``print_window`` 不调用它，也不要求窗口可见。
"""

import ctypes
import operator
from ctypes import wintypes

from PIL import Image

from easytrader.log import logger

__all__ = [
    "PW_CLIENTONLY",
    "PW_RENDERFULLCONTENT",
    "CaptureError",
    "crop_window_area",
    "ensure_rendered",
    "print_window",
]

# PrintWindow 标志
PW_CLIENTONLY = 1
# 2 = PW_RENDERFULLCONTENT：自绘窗口（Chromium/Electron/DirectComposition）
# 必须带这个标志；flag 0/1 往往只能得到全黑图。
PW_RENDERFULLCONTENT = 2

# GDI 对位图尺寸的上限，超过后 CreateCompatibleBitmap 会失败或静默降级。
MAX_DIMENSION = 32767

_SW_SHOW = 5
_SW_RESTORE = 9

_u = ctypes.WinDLL("user32", use_last_error=True)
_gdi = ctypes.WinDLL("gdi32", use_last_error=True)

_u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u.GetWindowRect.restype = wintypes.BOOL

_u.IsWindowVisible.argtypes = [wintypes.HWND]
_u.IsWindowVisible.restype = wintypes.BOOL

_u.GetWindowDC.argtypes = [wintypes.HWND]
_u.GetWindowDC.restype = wintypes.HDC

_u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u.ReleaseDC.restype = wintypes.INT

# BOOL PrintWindow(HWND, HDC, UINT) —— 必须显式声明，否则 64 位下返回值
# 与句柄宽度不一致，失败的调用可能被当成成功。
_u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_u.PrintWindow.restype = wintypes.BOOL

_gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi.DeleteObject.restype = wintypes.BOOL


class CaptureError(RuntimeError):
    """PrintWindow 抓图失败：窗口不可画、系统调用返回失败或像素格式不符。"""


def _win32ui():
    """返回 win32ui 模块。

    单独包一层是为了导入时机可控，也让离线测试可以整体替换这一层。
    """
    import win32ui

    return win32ui


def _last_error(where):
    """把 ctypes 记录的线程局部 last error 附加到错误信息里。"""
    return "{}（last error={}）".format(where, ctypes.get_last_error())


def _validate_hwnd(hwnd):
    """校验窗口句柄，返回 int。

    句柄为 0 / 负数时 GetWindowRect 会失败，但先在这里拒绝可以给出
    更明确的错误，也避免把无效句柄传给 GDI。
    """
    if isinstance(hwnd, bool):
        raise ValueError("窗口句柄无效: {!r}".format(hwnd))
    try:
        value = operator.index(hwnd)
    except TypeError:
        raise ValueError("窗口句柄无效: {!r}".format(hwnd))
    if value <= 0:
        raise ValueError("窗口句柄无效: {!r}".format(hwnd))
    return value


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if not _u.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise CaptureError(_last_error("GetWindowRect 失败 hwnd={}".format(hwnd)))
    return rect


def _window_size(hwnd):
    """窗口的 (宽, 高)；尺寸无效时抛 ValueError，而不是继续分配位图。"""
    rect = _window_rect(hwnd)
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise ValueError(
            "窗口尺寸无效: {}x{}（hwnd={}）".format(width, height, hwnd)
        )
    return width, height


def _to_dimension(value, label):
    try:
        dimension = operator.index(value)
    except TypeError:
        raise ValueError("{}无效: {!r}".format(label, value))
    if dimension <= 0:
        raise ValueError("{}无效: {}".format(label, dimension))
    if dimension > MAX_DIMENSION:
        raise ValueError(
            "{}超出 GDI 上限 {}: {}".format(label, MAX_DIMENSION, dimension)
        )
    return dimension


def _output_size(hwnd, width, height):
    """确定输出尺寸。

    - 两个尺寸都显式给出时不必读窗口矩形；
    - 缺省值取自窗口矩形；
    - 无论来源如何，最终尺寸都必须为正且不超过 GDI 上限，
      否则 CreateCompatibleBitmap 会静默产出 1x1 位图。
    """
    if width is None or height is None:
        window_width, window_height = _window_size(hwnd)
        if width is None:
            width = window_width
        if height is None:
            height = window_height
    return _to_dimension(width, "截图宽度"), _to_dimension(height, "截图高度")


def _bits_to_image(bitmap, width, height):
    """把已画好的 32bpp 兼容位图转成 PIL Image，全程不落盘。

    PrintWindow 画进内存 DC 的位图是 32bpp BGRX。逐行读取时用
    GetInfo() 给出的 bmWidthBytes 作 stride，行序自上而下
    （已在本机实测：逻辑坐标 (0, 0) 对应读出的第 0 行）。
    """
    info = bitmap.GetInfo()
    bits_per_pixel = info.get("bmBitsPixel", 0)
    if bits_per_pixel != 32:
        raise CaptureError(
            "不支持的位深: {}bpp（需要 32bpp 兼容位图）".format(bits_per_pixel)
        )

    stride = info.get("bmWidthBytes") or width * 4
    if stride < width * 4:
        raise CaptureError(
            "位图行宽异常: {} 字节，至少需要 {} 字节（{}x{}）".format(
                stride, width * 4, width, height
            )
        )

    bits = bitmap.GetBitmapBits(True)
    expected = stride * height
    if len(bits) != expected:
        raise CaptureError(
            "位图数据长度异常: {} 字节，期望 {} 字节（{}x{}）".format(
                len(bits), expected, width, height
            )
        )

    # frombytes 会复制一份数据，返回的 Image 不依赖位图和 DC 的生命周期。
    return Image.frombytes(
        "RGB", (width, height), bytes(bits), "raw", "BGRX", stride, 1
    )


def _cleanup(action, description):
    """执行清理动作；清理失败只记 debug，不能掩盖真正的异常。"""
    try:
        action()
    except Exception:
        logger.debug("截图资源清理失败: %s", description, exc_info=True)


def _capture_window(
    hwnd, width=None, height=None, flag=PW_RENDERFULLCONTENT,
    clear_color=0, require_success=True,
):
    """用 PrintWindow 把窗口内容画到内存位图，返回 RGB 的 PIL Image。

    只读：不显示、不移动、不激活、不前置窗口，可以在后台线程里对
    另一个虚拟桌面上的窗口调用。

    :param hwnd: 目标窗口句柄
    :param width: 输出宽度，默认取窗口宽度
    :param height: 输出高度，默认取窗口高度
    :param flag: PrintWindow 标志，默认 PW_RENDERFULLCONTENT
    :return: PIL.Image.Image，mode 为 "RGB"
    :raises ValueError: hwnd 或尺寸无效（在申请任何 GDI 资源之前抛出）
    :raises CaptureError: GetWindowDC / PrintWindow / 像素读取失败
    """
    _validate_hwnd(hwnd)
    width, height = _output_size(hwnd, width, height)

    win32ui = _win32ui()

    window_dc = None
    mem_dc = None
    previous_object = None
    bitmap_handle = None
    try:
        window_dc = _u.GetWindowDC(hwnd)
        if not window_dc:
            raise CaptureError(
                _last_error("GetWindowDC 失败 hwnd={}".format(hwnd))
            )

        # 由窗口 DC 派生的 PyCDC 包装只用于 CreateCompatibleDC /
        # CreateCompatibleBitmap。注意：不要对包装调用 DeleteDC()，
        # 它会连同窗口 DC 一起销毁，随后 ReleaseDC 必然失败（实测错误码 87）。
        window_dc_source = win32ui.CreateDCFromHandle(window_dc)
        if window_dc_source is None:
            raise CaptureError("CreateDCFromHandle 失败 hwnd={}".format(hwnd))

        mem_dc = window_dc_source.CreateCompatibleDC()
        if mem_dc is None:
            raise CaptureError("CreateCompatibleDC 失败 hwnd={}".format(hwnd))

        bitmap = win32ui.CreateBitmap()
        if bitmap is None:
            raise CaptureError("CreateBitmap 失败 hwnd={}".format(hwnd))

        # pywin32 的 CreateCompatibleBitmap 不检查返回值，尺寸非法时会静默
        # 留下一个 1x1 位图，所以尺寸必须在上面的 _output_size 里先校验。
        bitmap.CreateCompatibleBitmap(window_dc_source, width, height)
        bitmap_handle = bitmap.GetHandle()
        if not bitmap_handle:
            raise CaptureError(
                "CreateCompatibleBitmap 失败 {}x{}".format(width, height)
            )

        # 保存 DC 里原有的 GDI 对象：位图仍被选中时 DeleteObject 会失败，
        # 必须先把它换回来才能真正释放。
        previous_object = mem_dc.SelectObject(bitmap)
        if not previous_object:
            raise CaptureError(
                "SelectObject 未返回原有 GDI 对象 hwnd={}".format(hwnd)
            )

        # 新分配的位图内容未定义，先清空，避免把随机内存当成图像返回。
        mem_dc.FillSolidRect((0, 0, width, height), clear_color)

        ctypes.set_last_error(0)
        rendered = bool(_u.PrintWindow(hwnd, mem_dc.GetSafeHdc(), int(flag)))
        if require_success and not rendered:
            raise CaptureError(
                _last_error(
                    "PrintWindow 失败 hwnd={} size={}x{} flag={}".format(
                        hwnd, width, height, flag
                    )
                )
            )

        return _bits_to_image(bitmap, width, height), rendered
    finally:
        # 顺序：把原对象选回 DC -> 删除位图 -> 删除内存 DC -> 释放窗口 DC。
        # 逆序释放，任何一步失败都不影响后面几步。
        if mem_dc is not None and previous_object:
            _cleanup(
                lambda: mem_dc.SelectObject(previous_object),
                "还原 DC 原有 GDI 对象",
            )

        if bitmap_handle:
            _cleanup(
                lambda: _gdi.DeleteObject(bitmap_handle),
                "删除位图句柄 {}".format(bitmap_handle),
            )

        if mem_dc is not None:
            _cleanup(mem_dc.DeleteDC, "删除内存 DC")

        if window_dc:
            _cleanup(
                lambda: _u.ReleaseDC(hwnd, window_dc),
                "释放窗口 DC hwnd={}".format(hwnd),
            )


def print_window(hwnd, width=None, height=None, flag=PW_RENDERFULLCONTENT):
    """Capture a window; a failed API return always raises CaptureError."""
    image, _ = _capture_window(hwnd, width, height, flag)
    return image


def verified_control_image(hwnd):
    """Accept only a stable, fully overwritten captcha image.

    A custom control can paint while returning zero. Two differently cleared
    surfaces must produce byte-for-byte identical, nonuniform RGB pixels. An
    unpainted or partially painted surface retains its distinct clear color
    and is rejected. This does not read the screen or render a parent dialog.
    """
    first, _ = _capture_window(
        hwnd, clear_color=0x00FF00FF, require_success=False
    )
    second, _ = _capture_window(
        hwnd, clear_color=0x0000FF00, require_success=False
    )
    if first.size != second.size or first.tobytes() != second.tobytes():
        raise CaptureError("验证码绘图未完整覆盖底色或图像正在变化")
    if all(high - low < 8 for low, high in second.getextrema()):
        raise CaptureError("验证码绘图为空白")
    return second


def ensure_rendered(hwnd):
    """确保窗口处于可见状态（历史 API，会改变窗口显示状态）。

    隐藏窗口上 PrintWindow 可能只得到全黑图，所以前台抓图路径会先调用它。
    但它内部调用 ``ShowWindow``，会显示窗口并可能抢焦点；
    后台 / 跨桌面场景不要调用。``print_window`` 不使用它。
    """
    try:
        if not _u.IsWindowVisible(hwnd):
            _u.ShowWindow(hwnd, _SW_SHOW)
            _u.ShowWindow(hwnd, _SW_RESTORE)
        return bool(_u.IsWindowVisible(hwnd))
    except Exception:
        return False


def crop_window_area(parent_image, parent_hwnd, child_hwnd):
    """从父窗口截图中裁出子控件所在的区域。

    用于抓验证码图片这类「不是独立窗口、只是绘制区域」的元素。
    坐标全部来自 GetWindowRect，只读，不依赖控件是否可见。

    子控件完全在父截图之外（例如窗口刚被移动、截图与 GetWindowRect 之间
    发生了位移）时抛 CaptureError，而不是返回一张 PIL 补黑的空图，
    避免把黑图当验证码送进识别。
    """
    _validate_hwnd(parent_hwnd)
    _validate_hwnd(child_hwnd)

    parent_rect = _window_rect(parent_hwnd)
    child_rect = _window_rect(child_hwnd)

    left = child_rect.left - parent_rect.left
    top = child_rect.top - parent_rect.top
    right = left + (child_rect.right - child_rect.left)
    bottom = top + (child_rect.bottom - child_rect.top)

    if right <= left or bottom <= top:
        raise CaptureError(
            "子控件尺寸为空: {}x{}（child hwnd={}）".format(
                right - left, bottom - top, child_hwnd
            )
        )

    image_width, image_height = parent_image.size
    # PIL 对越界区域会补黑，这里先裁到图像范围内，返回尺寸才是真实的。
    box = (
        max(0, min(left, image_width)),
        max(0, min(top, image_height)),
        max(0, min(right, image_width)),
        max(0, min(bottom, image_height)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise CaptureError(
            "子控件不在父窗口截图范围内: {} vs 截图 {}x{}".format(
                (left, top, right, bottom), image_width, image_height
            )
        )
    return parent_image.crop(box)
