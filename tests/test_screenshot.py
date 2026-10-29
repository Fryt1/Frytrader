# -*- coding: utf-8 -*-
"""screenshot 模块的离线测试：失败路径、尺寸校验、GDI 清理、不激活、不落盘。

全部用假 GDI 层 + mock，不碰真实窗口、不注入输入、不连接任何交易客户端，
因此可以在没有桌面会话的机器上跑。
"""

import ctypes
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from easytrader import screenshot

HWND = 0x000A0B0C
WINDOW_DC = 0x00DC0001
MEM_DC = 0x00DC0002
BITMAP_HANDLE = 0x00B17001
PREVIOUS_OBJECT = 0x00B17002
WIDTH = 4
HEIGHT = 3


class Rect:
    def __init__(self, left=0, top=0, right=WIDTH, bottom=HEIGHT):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeBitmap:
    """记录 pywin32 位图上的调用，并返回可控的像素数据。"""

    def __init__(self, log, bits=None, bits_per_pixel=32, width_bytes=None):
        self.log = log
        self.handle = BITMAP_HANDLE
        self.bits = bits
        self.bits_per_pixel = bits_per_pixel
        self.forced_width_bytes = width_bytes
        self.created_size = None

    def CreateCompatibleBitmap(self, source_dc, width, height):
        self.log.append(("CreateCompatibleBitmap", width, height))
        self.created_size = (width, height)

    def GetHandle(self):
        return self.handle

    def GetInfo(self):
        width, height = self.created_size or (WIDTH, HEIGHT)
        width_bytes = (
            self.forced_width_bytes
            if self.forced_width_bytes is not None
            else width * 4
        )
        return {
            "bmType": 0,
            "bmWidth": width,
            "bmHeight": height,
            "bmWidthBytes": width_bytes,
            "bmPlanes": 1,
            "bmBitsPixel": self.bits_per_pixel,
        }

    def GetBitmapBits(self, _signed):
        self.log.append(("GetBitmapBits",))
        if self.bits is not None:
            return self.bits
        width, height = self.created_size or (WIDTH, HEIGHT)
        return bytes([0x40, 0x30, 0x20, 0x00]) * (width * height)


class FakeDC:
    """假的 PyCDC / 内存 DC，记录 select / 删除顺序。"""

    def __init__(self, log, name, hdc=MEM_DC):
        self.log = log
        self.name = name
        self.hdc = hdc
        self.selected = []

    def GetSafeHdc(self):
        return self.hdc

    def CreateCompatibleDC(self):
        self.log.append(("CreateCompatibleDC",))
        return FakeDC(self.log, "mem-dc", hdc=MEM_DC)

    def SelectObject(self, obj):
        handle = getattr(obj, "GetHandle", None)
        value = handle() if handle else obj
        if not value:
            return None
        previous = self.selected[-1] if self.selected else None
        self.selected.append(value)
        self.log.append(("SelectObject", self.name, value))
        return previous if previous is not None else PREVIOUS_OBJECT

    def FillSolidRect(self, rect, color):
        self.log.append(("FillSolidRect", tuple(rect), color))

    def DeleteDC(self):
        self.log.append(("DeleteDC", self.name))


class FakeWin32Ui:
    def __init__(self, log, bitmap=None, failing=None):
        self.log = log
        self.bitmap = bitmap
        self.failing = failing or set()
        self.bitmaps_created = 0

    def CreateDCFromHandle(self, hdc):
        self.log.append(("CreateDCFromHandle", hdc))
        if "CreateDCFromHandle" in self.failing:
            return None
        return FakeDC(self.log, "window-dc", hdc=hdc)

    def CreateBitmap(self):
        if "CreateBitmap" in self.failing:
            return None
        self.bitmaps_created += 1
        if self.bitmap is not None:
            return self.bitmap
        return FakeBitmap(self.log)


class ScreenshotTestCase(unittest.TestCase):
    """安装假的 user32 / gdi32 / win32ui，让 print_window 完全离线运行。"""

    def setUp(self):
        self.log = []
        self.rect = Rect()
        self.rect_ok = True
        self.window_dc = WINDOW_DC
        self.print_window_ok = True
        self.print_window_result = 1
        self.release_dc_result = 1
        self.delete_object_result = 1
        self.bitmap = FakeBitmap(self.log)
        self.failing = set()
        self.win32ui = FakeWin32Ui(self.log, bitmap=self.bitmap, failing=self.failing)

        patches = [
            mock.patch.object(screenshot, "_win32ui", lambda: self.win32ui),
            mock.patch.object(screenshot, "_u", self._fake_user32()),
            mock.patch.object(screenshot, "_gdi", self._fake_gdi()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _fake_user32(self):
        test = self

        class FakeUser32:
            def GetWindowRect(self, hwnd, rect_pointer):
                test.log.append(("GetWindowRect", hwnd))
                if not test.rect_ok:
                    return 0
                # byref(rect) 的 _obj 就是调用方那个 wintypes.RECT
                target = rect_pointer._obj
                target.left = test.rect.left
                target.top = test.rect.top
                target.right = test.rect.right
                target.bottom = test.rect.bottom
                return 1

            def GetWindowDC(self, hwnd):
                test.log.append(("GetWindowDC", hwnd))
                return test.window_dc

            def PrintWindow(self, hwnd, hdc, flag):
                test.log.append(("PrintWindow", hwnd, hdc, flag))
                return test.print_window_result if test.print_window_ok else 0

            def ReleaseDC(self, hwnd, hdc):
                test.log.append(("ReleaseDC", hwnd, hdc))
                return test.release_dc_result

        return FakeUser32()

    def _fake_gdi(self):
        test = self

        class FakeGdi:
            def DeleteObject(self, handle):
                test.log.append(("DeleteObject", handle))
                return test.delete_object_result

        return FakeGdi()

    def names(self):
        return [entry[0] for entry in self.log]

    def assert_cleaned_up(self):
        """位图必须换出后才能删除，DC 与窗口 DC 都必须释放。"""
        self.assertIn(("DeleteObject", BITMAP_HANDLE), self.log)
        self.assertIn(("DeleteDC", "mem-dc"), self.log)
        self.assertIn(("ReleaseDC", HWND, WINDOW_DC), self.log)

        delete_object_at = self.log.index(("DeleteObject", BITMAP_HANDLE))
        delete_dc_at = self.log.index(("DeleteDC", "mem-dc"))
        release_at = self.log.index(("ReleaseDC", HWND, WINDOW_DC))
        # 逆序释放：先还对象、再删位图、再删内存 DC、最后释放窗口 DC
        self.assertLess(delete_object_at, delete_dc_at)
        self.assertLess(delete_dc_at, release_at)

        # 删除位图之前必须已经把它从 DC 里换出来
        restore = [
            entry
            for entry in self.log[:delete_object_at]
            if entry[0] == "SelectObject" and entry[2] == PREVIOUS_OBJECT
        ]
        self.assertTrue(restore, "删除位图前必须把原 GDI 对象选回 DC")


class TestWin32Declarations(unittest.TestCase):
    """ctypes 调用必须显式声明签名。

    这台机器上 ctypes.windll 的默认 restype 是 c_int，而 BOOL PrintWindow
    的返回值在 64 位下与句柄宽度不同；未声明时失败的调用可能被当成成功，
    hwnd/HDC 也可能被截断成 32 位。
    """

    def test_printwindow_is_typed_bool_hwnd_hdc_uint(self):
        from ctypes import wintypes

        prototype = screenshot._u.PrintWindow
        self.assertEqual(
            prototype.argtypes, [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        )
        self.assertEqual(prototype.restype, wintypes.BOOL)

    def test_handle_returning_calls_use_pointer_sized_restype(self):
        from ctypes import wintypes

        self.assertEqual(screenshot._u.GetWindowDC.restype, wintypes.HDC)
        self.assertEqual(
            screenshot._u.GetWindowRect.argtypes,
            [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
        )
        self.assertEqual(screenshot._gdi.DeleteObject.argtypes, [wintypes.HGDIOBJ])

    def test_release_dc_takes_the_matching_window_and_dc(self):
        from ctypes import wintypes

        self.assertEqual(
            screenshot._u.ReleaseDC.argtypes, [wintypes.HWND, wintypes.HDC]
        )

    def test_windll_uses_last_error_capture(self):
        """use_last_error=True 才能拿到 GetLastError，用于错误信息。"""
        # ctypes.WinDLL 实例不暴露该标志，改为断言没有退回未声明的 windll
        with open(screenshot.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertNotIn("ctypes.windll", source)
        self.assertIn('ctypes.WinDLL("user32", use_last_error=True)', source)
        self.assertIn('ctypes.WinDLL("gdi32", use_last_error=True)', source)


class TestSuccessfulCapture(ScreenshotTestCase):
    def test_returns_rgb_image_of_window_size(self):
        image = screenshot.print_window(HWND)

        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (WIDTH, HEIGHT))
        # 默认使用窗口尺寸，并使用 PW_RENDERFULLCONTENT
        self.assertIn(("CreateCompatibleBitmap", WIDTH, HEIGHT), self.log)
        self.assertIn(("PrintWindow", HWND, MEM_DC, screenshot.PW_RENDERFULLCONTENT), self.log)

    def test_explicit_size_overrides_window_size(self):
        image = screenshot.print_window(HWND, width=8, height=6)

        self.assertEqual(image.size, (8, 6))
        self.assertIn(("CreateCompatibleBitmap", 8, 6), self.log)

    def test_flag_is_forwarded(self):
        screenshot.print_window(HWND, flag=screenshot.PW_CLIENTONLY)

        self.assertIn(("PrintWindow", HWND, MEM_DC, screenshot.PW_CLIENTONLY), self.log)

    def test_bitmap_is_cleared_before_printwindow(self):
        screenshot.print_window(HWND)

        order = self.names()
        self.assertLess(order.index("FillSolidRect"), order.index("PrintWindow"))

    def test_pixels_are_decoded_from_memory_bitmap(self):
        self.bitmap.bits = bytes([0x10, 0x20, 0x30, 0x00]) * (WIDTH * HEIGHT)

        image = screenshot.print_window(HWND)

        # BGRX：内存里的 B/G/R 反向还原成 RGB
        self.assertEqual(image.getpixel((0, 0)), (0x30, 0x20, 0x10))

    def test_capture_does_not_touch_the_filesystem(self):
        """抓图不能写固定临时文件：同一进程并发抓多个控件会互相覆盖。"""
        workdir = tempfile.mkdtemp(prefix="shot-test-")
        self.addCleanup(shutil.rmtree, workdir, True)

        before = set(os.listdir(workdir))
        cwd = os.getcwd()
        os.chdir(workdir)
        try:
            image = screenshot.print_window(HWND)
            created = set(os.listdir(workdir)) - before
        finally:
            os.chdir(cwd)

        self.assertEqual(created, set(), "抓图不应产生任何文件")
        self.assertIsInstance(image, Image.Image)
        # 不依赖文件就意味着没有 filename 属性（Image.open 的产物才有）
        self.assertFalse(hasattr(image, "filename"))
        with open(screenshot.__file__, encoding="utf-8") as source_file:
            self.assertNotIn("tempfile", source_file.read())

    def test_repeated_captures_use_distinct_buffers(self):
        first = screenshot.print_window(HWND)
        second = screenshot.print_window(HWND)

        self.assertIsNot(first, second)
        self.assertEqual(self.win32ui.bitmaps_created, 2)
        # 每次抓图都各自释放自己的位图与 DC
        self.assertEqual(self.names().count("DeleteObject"), 2)
        self.assertEqual(self.names().count("ReleaseDC"), 2)


class TestVerifiedControlImage(ScreenshotTestCase):
    def test_zero_return_with_full_stable_pixels_is_verified(self):
        self.print_window_ok = False
        self.bitmap.bits = (
            b"\xff\x78\x00\x00" + b"\xff\xff\xff\x00" * 11
        )
        image = screenshot.verified_control_image(HWND)
        self.assertEqual(image.getpixel((0, 0)), (0, 120, 255))
        clears = [entry[2] for entry in self.log if entry[0] == "FillSolidRect"]
        self.assertEqual(clears, [0x00FF00FF, 0x0000FF00])
        self.assertEqual(self.names().count("ReleaseDC"), 2)
        self.assertEqual(self.names().count("DeleteObject"), 2)

    def test_unpainted_or_partially_painted_surfaces_are_rejected(self):
        first = Image.new("RGB", (4, 3), "white")
        first.putpixel((0, 0), (0, 120, 255))
        second = first.copy()
        first.putpixel((3, 2), (255, 0, 255))
        second.putpixel((3, 2), (0, 255, 0))
        with mock.patch.object(
            screenshot, "_capture_window",
            side_effect=[(first, False), (second, False)],
        ):
            with self.assertRaises(screenshot.CaptureError):
                screenshot.verified_control_image(HWND)

    def test_blank_but_fully_painted_bitmap_is_not_a_captcha(self):
        self.print_window_ok = False
        with self.assertRaisesRegex(screenshot.CaptureError, "空白"):
            screenshot.verified_control_image(HWND)

    def test_verification_never_uses_parent_or_screen_capture(self):
        with mock.patch.object(screenshot, "ensure_rendered") as show:
            self.bitmap.bits = (
                b"\xff\x78\x00\x00" + b"\xff\xff\xff\x00" * 11
            )
            screenshot.verified_control_image(HWND)
        show.assert_not_called()
        for call in self.log:
            if call[0] in ("GetWindowRect", "PrintWindow"):
                self.assertEqual(call[1], HWND)


class TestNoActivation(ScreenshotTestCase):
    """后台抓图绝不能显示/激活/前置窗口，也不能注入全局输入。"""

    FORBIDDEN = (
        "ShowWindow",
        "ShowWindowAsync",
        "SetForegroundWindow",
        "SetActiveWindow",
        "SetWindowPos",
        "BringWindowToTop",
        "SetFocus",
        "AttachThreadInput",
        "SendInput",
        "BlockInput",
    )

    def test_capture_never_requests_activation(self):
        with mock.patch.object(
            ctypes, "WinDLL", wraps=ctypes.WinDLL
        ) as windll:
            screenshot.print_window(HWND)

        for call in windll.call_args_list:
            for name in self.FORBIDDEN:
                self.assertNotIn(name, call.args[0])

        # assert_cleaned_up 之后再确认：真实调用的名字里没有这些函数
        self.assertFalse(
            set(self.names()) & set(self.FORBIDDEN),
            "print_window 不应调用任何激活/显示函数",
        )

    def test_print_window_does_not_call_ensure_rendered(self):
        with mock.patch.object(screenshot, "ensure_rendered") as guarded:
            screenshot.print_window(HWND)

        guarded.assert_not_called()

    def test_module_import_keeps_windows_state_untouched(self):
        """模块级入口不应对窗口做任何写操作（只读抓图）。"""
        with open(screenshot.__file__, encoding="utf-8") as source_file:
            source = source_file.read()

        capture_section = source.split("def print_window", 1)[1].split(
            "def ensure_rendered", 1
        )[0]
        for name in self.FORBIDDEN:
            self.assertNotIn(name, capture_section)


class TestPrintWindowFailure(ScreenshotTestCase):
    def test_failed_printwindow_raises_and_reports_winerror(self):
        self.print_window_result = 0
        self.print_window_ok = False

        with mock.patch.object(ctypes, "get_last_error", return_value=87):
            with self.assertRaises(screenshot.CaptureError) as caught:
                screenshot.print_window(HWND)

        message = str(caught.exception)
        self.assertIn("PrintWindow 失败", message)
        self.assertIn("87", message)

    def test_failed_printwindow_still_releases_every_resource(self):
        self.print_window_ok = False
        self.print_window_result = 0

        with self.assertRaises(screenshot.CaptureError):
            screenshot.print_window(HWND)

        self.assert_cleaned_up()

    def test_failed_printwindow_does_not_decode_pixels(self):
        self.print_window_ok = False
        self.print_window_result = 0

        with self.assertRaises(screenshot.CaptureError):
            screenshot.print_window(HWND)

        self.assertNotIn("GetBitmapBits", self.names())

    def test_get_window_dc_failure_raises_and_skips_cleanup_of_missing_dc(self):
        self.window_dc = 0

        with self.assertRaises(screenshot.CaptureError) as caught:
            screenshot.print_window(HWND)

        self.assertIn("GetWindowDC 失败", str(caught.exception))
        self.assertNotIn(("ReleaseDC", HWND, WINDOW_DC), self.log)
        self.assertNotIn("CreateCompatibleDC", self.names())

    def test_get_window_rect_failure_raises(self):
        self.rect_ok = False

        with mock.patch.object(ctypes, "get_last_error", return_value=1400):
            with self.assertRaises(screenshot.CaptureError) as caught:
                screenshot.print_window(HWND)

        self.assertIn("GetWindowRect 失败", str(caught.exception))
        self.assertIn("1400", str(caught.exception))
        self.assertNotIn("GetWindowDC", self.names())

    def test_create_compatible_dc_failure_is_reported(self):
        with mock.patch.object(FakeDC, "CreateCompatibleDC", return_value=None):
            with self.assertRaises(screenshot.CaptureError) as caught:
                screenshot.print_window(HWND)

        self.assertIn("CreateCompatibleDC 失败", str(caught.exception))
        # 内存 DC 没建起来，但窗口 DC 仍必须释放
        self.assertIn(("ReleaseDC", HWND, WINDOW_DC), self.log)
        self.assertNotIn(("DeleteDC", "mem-dc"), self.log)

    def test_select_object_failure_is_reported_and_cleans_up(self):
        with mock.patch.object(FakeDC, "SelectObject", return_value=None):
            with self.assertRaises(screenshot.CaptureError) as caught:
                screenshot.print_window(HWND)

        # 没有拿到原对象就无法安全删除位图，但其余资源必须释放
        self.assertIn("SelectObject", str(caught.exception))
        self.assertIn(("DeleteDC", "mem-dc"), self.log)
        self.assertIn(("ReleaseDC", HWND, WINDOW_DC), self.log)

    def test_one_failing_cleanup_step_does_not_skip_the_rest(self):
        """清理本身出错时，后续清理仍要执行，真正的异常不能被掩盖。"""
        self.print_window_ok = False
        self.print_window_result = 0
        calls = {"n": 0}
        real_select = FakeDC.SelectObject

        def select_then_fail(self, obj):
            # 第一次（选入位图）正常，第二次（还原旧对象）在清理阶段抛错
            calls["n"] += 1
            if calls["n"] == 1:
                return real_select(self, obj)
            raise OSError("DC 已失效")

        with mock.patch.object(FakeDC, "SelectObject", select_then_fail):
            with self.assertRaises(screenshot.CaptureError) as caught:
                screenshot.print_window(HWND)

        self.assertIn("PrintWindow 失败", str(caught.exception))
        self.assertIn(("DeleteObject", BITMAP_HANDLE), self.log)
        self.assertIn(("DeleteDC", "mem-dc"), self.log)
        self.assertIn(("ReleaseDC", HWND, WINDOW_DC), self.log)

    def test_failed_release_dc_does_not_break_the_capture(self):
        self.release_dc_result = 0

        with mock.patch.object(ctypes, "get_last_error", return_value=87):
            image = screenshot.print_window(HWND)

        self.assertEqual(image.size, (WIDTH, HEIGHT))
        self.assertIn(("ReleaseDC", HWND, WINDOW_DC), self.log)


class TestInvalidDimensions(ScreenshotTestCase):
    def assert_no_gdi_allocated(self):
        for name in ("GetWindowDC", "CreateCompatibleDC", "CreateBitmap",
                     "CreateCompatibleBitmap"):
            self.assertNotIn(name, self.names(), "尺寸校验必须发生在分配 GDI 资源之前")

    def test_zero_window_size_raises(self):
        self.rect = Rect(10, 10, 10, 40)

        with self.assertRaises(ValueError):
            screenshot.print_window(HWND)
        self.assert_no_gdi_allocated()

    def test_negative_window_size_raises(self):
        self.rect = Rect(100, 100, 40, 60)

        with self.assertRaises(ValueError):
            screenshot.print_window(HWND)
        self.assert_no_gdi_allocated()

    def test_zero_explicit_width_raises(self):
        with self.assertRaises(ValueError):
            screenshot.print_window(HWND, width=0)
        self.assert_no_gdi_allocated()

    def test_negative_explicit_height_raises(self):
        with self.assertRaises(ValueError):
            screenshot.print_window(HWND, height=-5)
        self.assert_no_gdi_allocated()

    def test_non_numeric_dimension_raises(self):
        with self.assertRaises(ValueError):
            screenshot.print_window(HWND, width="640")
        self.assert_no_gdi_allocated()

    def test_oversized_dimension_raises_before_allocating(self):
        with self.assertRaises(ValueError):
            screenshot.print_window(HWND, width=screenshot.MAX_DIMENSION + 1)
        self.assert_no_gdi_allocated()

    def test_invalid_hwnd_raises_value_error(self):
        for hwnd in (0, -1, None, "0x10", True):
            with self.subTest(hwnd=hwnd):
                with self.assertRaises(ValueError):
                    screenshot.print_window(hwnd)
        self.assertEqual(self.log, [])

    def test_bitmap_data_length_mismatch_raises(self):
        """位图返回的字节数不足时不能拿半张图当结果。"""
        self.bitmap.bits = b"\x00" * (WIDTH * HEIGHT * 4 - 1)

        with self.assertRaises(screenshot.CaptureError):
            screenshot.print_window(HWND)

        self.assert_cleaned_up()

    def test_unexpected_bit_depth_raises(self):
        self.bitmap.bits_per_pixel = 24
        self.bitmap.bits = b"\x00" * (WIDTH * HEIGHT * 3)

        with self.assertRaises(screenshot.CaptureError):
            screenshot.print_window(HWND)

        self.assert_cleaned_up()


class TestCropWindowArea(ScreenshotTestCase):
    def test_crops_child_rect_relative_to_parent(self):
        parent = Image.new("RGB", (100, 100), (0, 0, 0))

        def rect_for(hwnd, _pointer=None):
            if hwnd == 1:
                return Rect(10, 20, 110, 120)
            return Rect(30, 50, 70, 90)

        with mock.patch.object(screenshot, "_window_rect", side_effect=rect_for):
            cropped = screenshot.crop_window_area(parent, 1, 2)

        # 子窗口相对父窗口偏移 (20, 30)，尺寸 40x40
        self.assertEqual(cropped.size, (40, 40))
        self.assertEqual(cropped.tobytes(), parent.crop((20, 30, 60, 70)).tobytes())

    def test_invalid_hwnd_raises(self):
        parent = Image.new("RGB", (10, 10))
        with self.assertRaises(ValueError):
            screenshot.crop_window_area(parent, 0, 2)

    def test_rect_failure_raises_capture_error(self):
        parent = Image.new("RGB", (10, 10))
        self.rect_ok = False

        with self.assertRaises(screenshot.CaptureError):
            screenshot.crop_window_area(parent, 1, 2)

    def test_empty_child_rect_raises_instead_of_returning_blank(self):
        parent = Image.new("RGB", (100, 100))

        def rect_for(hwnd, _pointer=None):
            return Rect(10, 10, 110, 120) if hwnd == 1 else Rect(50, 50, 50, 50)

        with mock.patch.object(screenshot, "_window_rect", side_effect=rect_for):
            with self.assertRaises(screenshot.CaptureError):
                screenshot.crop_window_area(parent, 1, 2)

    def test_child_outside_parent_image_raises(self):
        """PIL 会把越界区域补黑，不能让黑图冒充验证码。"""
        parent = Image.new("RGB", (20, 20))

        def rect_for(hwnd, _pointer=None):
            return Rect(0, 0, 20, 20) if hwnd == 1 else Rect(100, 100, 140, 140)

        with mock.patch.object(screenshot, "_window_rect", side_effect=rect_for):
            with self.assertRaises(screenshot.CaptureError):
                screenshot.crop_window_area(parent, 1, 2)

    def test_partially_visible_child_is_clipped_to_the_image(self):
        parent = Image.new("RGB", (30, 30), (7, 7, 7))

        def rect_for(hwnd, _pointer=None):
            return Rect(0, 0, 30, 30) if hwnd == 1 else Rect(20, 20, 60, 60)

        with mock.patch.object(screenshot, "_window_rect", side_effect=rect_for):
            cropped = screenshot.crop_window_area(parent, 1, 2)

        self.assertEqual(cropped.size, (10, 10))
        self.assertEqual(cropped.getpixel((0, 0)), (7, 7, 7))


class TestEnsureRendered(ScreenshotTestCase):
    """历史 API：确保可见，允许改变显示状态；但必须容错而不是崩溃。"""

    def test_returns_visibility_and_never_raises(self):
        with mock.patch.object(screenshot._u, "IsWindowVisible", return_value=0,
                               create=True), \
             mock.patch.object(screenshot._u, "ShowWindow", create=True):
            self.assertFalse(screenshot.ensure_rendered(HWND))

    def test_swallows_exceptions(self):
        with mock.patch.object(screenshot._u, "IsWindowVisible",
                               side_effect=OSError("boom"), create=True):
            self.assertFalse(screenshot.ensure_rendered(HWND))


if __name__ == "__main__":
    unittest.main()
