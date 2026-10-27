# -*- coding: utf-8 -*-
"""窗口截图工具：不依赖真实屏幕的前台/可见状态。

用途
----
同花顺的验证码图片在以下两种情形都无法用 Pillow 的 ImageGrab 抓取：
- 窗口被其他程序遮挡，或被移到屏幕外；
- 窗口运行在隐藏桌面（独立 Desktop）上。

这两种情况下 ImageGrab / pywinauto 的 capture_as_image 会抛
"screen grab failed"。而 PrintWindow 让窗口把内容画到内存 DC，
不依赖窗口是否真的显示在屏幕上，因此可以在后台取到图片。

两个实测要点
------------
1. 必须用 PW_RENDERFULLCONTENT（flag=2）。flag 0/1 对自绘窗口
   只会返回全黑图。
2. 窗口必须带 WS_VISIBLE，否则同样只能得到全黑图。
   客户端在某些情况下会去掉这个样式，所以截图前用 ensure_rendered 恢复。

关于隐藏桌面
------------
曾经尝试把同花顺整体跑在独立桌面（CreateDesktop）上彻底隔离输入，
但实测受限：
- 该桌面上的窗口默认不渲染，需要显式 ShowWindow 才能抓到图；
- 登录态随进程存亡，进程退出即丢失；
- 切换桌面会让用户离开自己的工作桌面，风险高。

结论是该方案不适用于日常使用，因此这里只保留其中确实有用的截图能力。
"""

import ctypes
import os
import tempfile

from PIL import Image

u = ctypes.windll.user32

# PrintWindow 标志：2 = PW_RENDERFULLCONTENT
PW_RENDERFULLCONTENT = 2


def ensure_rendered(hwnd):
    """确保窗口处于可见状态，否则 PrintWindow 只会得到全黑图。

    客户端在隐藏桌面上启动时会把 WS_VISIBLE 去掉，
    这里显式恢复，让 GDI 有内容可画。
    """
    try:
        if not u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, 5)  # SW_SHOW
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(u.IsWindowVisible(hwnd))
    except Exception:
        return False


def print_window(hwnd, width=None, height=None, flag=PW_RENDERFULLCONTENT):
    """用 PrintWindow 抓取窗口内容，返回 PIL Image。

    :param hwnd: 目标窗口句柄
    :param width: 输出宽度，默认取窗口宽度
    :param height: 输出高度，默认取窗口高度
    :param flag: PrintWindow 标志，默认 PW_RENDERFULLCONTENT
    """
    import win32gui
    import win32ui

    rect = win32gui.GetWindowRect(hwnd)
    w = width or (rect[2] - rect[0])
    h = height or (rect[3] - rect[1])
    if w <= 0 or h <= 0:
        raise ValueError("窗口尺寸无效: {}x{}".format(w, h))

    window_dc = win32gui.GetWindowDC(hwnd)
    try:
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)
            u.PrintWindow(hwnd, save_dc.GetSafeHdc(), flag)

            temp_path = os.path.join(
                tempfile.gettempdir(), "easytrader_printwindow.bmp"
            )
            bitmap.SaveBitmapFile(save_dc, temp_path)
            image = Image.open(temp_path).convert("RGB")
            image.load()
            return image
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
    finally:
        win32gui.ReleaseDC(hwnd, window_dc)


def crop_window_area(parent_image, parent_hwnd, child_hwnd):
    """从父窗口截图中裁出子控件所在的区域。

    用于抓验证码图片这类「不是独立窗口、只是绘制区域」的元素。
    """
    import win32gui

    parent_rect = win32gui.GetWindowRect(parent_hwnd)
    child_rect = win32gui.GetWindowRect(child_hwnd)
    left = child_rect[0] - parent_rect[0]
    top = child_rect[1] - parent_rect[1]
    right = left + (child_rect[2] - child_rect[0])
    bottom = top + (child_rect[3] - child_rect[1])
    return parent_image.crop((left, top, right, bottom))
