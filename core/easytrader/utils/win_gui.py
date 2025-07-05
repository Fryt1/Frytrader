# coding:utf-8
try:
    from pywinauto import win32defines
    from pywinauto.win32functions import SetForegroundWindow, ShowWindow
except ImportError:
    # 备用方案：直接使用 win32gui
    import win32gui
    import win32con as win32defines
    SetForegroundWindow = win32gui.SetForegroundWindow
    ShowWindow = win32gui.ShowWindow
