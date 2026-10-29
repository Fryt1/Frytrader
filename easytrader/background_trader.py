"""Read-only Voyager automation on a Windows Task View virtual desktop.

This adapter has no foreground/keyboard fallback and does not inherit the
order-capable ClientTrader. Its worker owns COM objects and serializes reads.
"""

import atexit
import io
import queue
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from easytrader.background_runtime import (
    AutoHotkeyControls,
    BackgroundError,
    BackgroundInterference,
)
from easytrader.config.client import CommonConfig
from easytrader.grid_strategies import _drop_empty_rows
from easytrader.utils.captcha import captcha_recognize_improved

PAGES = {
    "position": ("资金股票", ("证券代码", "股票余额")),
    "today_entrusts": ("当日委托", ("合同编号", "委托数量")),
    "today_trades": ("当日成交", ("成交编号", "成交金额")),
}


@dataclass(frozen=True)
class BackgroundOptions:
    ahk_path: object = None
    desktop_name: str = "Frytrader 后台"
    timeout: float = 15.0
    captcha_retries: int = 5
    poll_interval: float = 0.02
    page_settle: float = 0.2
    tesseract_path: object = None
    restore_on_close: bool = False

    def __post_init__(self):
        if not isinstance(self.restore_on_close, bool):
            raise ValueError("restore_on_close 必须是布尔值")
        if (
            not isinstance(self.desktop_name, str)
            or not self.desktop_name.strip()
        ):
            raise ValueError("desktop_name 不能为空")
        if not 2 <= self.timeout <= 120:
            raise ValueError("timeout 必须在 2 到 120 秒之间")
        if (
            isinstance(self.captcha_retries, bool)
            or not isinstance(self.captcha_retries, int)
            or not 1 <= self.captcha_retries <= 10
        ):
            raise ValueError("captcha_retries 必须为 1 到 10 的整数")
        if not 0.01 <= self.poll_interval <= 0.2:
            raise ValueError("poll_interval 必须在 0.01 到 0.2 秒之间")
        if not 0 <= self.page_settle <= 2:
            raise ValueError("page_settle 必须在 0 到 2 秒之间")


class QueryGuard:
    """Observe desktop/foreground without moving them back."""

    def __init__(self, backend, deadline, cancelled):
        self.backend = backend
        self.deadline = deadline
        self.cancelled = cancelled
        self.source = backend.current_desktop_id()
        self.problem = None
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.last_sample = time.monotonic()
        self.thread = None
        self.activation_guard = None

    def sample(self):
        if self.backend.current_desktop_id() != self.source:
            raise BackgroundInterference(
                "检测到任务视图桌面变化，后台读取已停止"
            )
        if self.backend.foreground_pid() in (
            self.backend.owner_pid,
            self.backend.trader_pid,
        ):
            raise BackgroundInterference("远航版激活了前台，后台读取已停止")
        self.last_sample = time.monotonic()

    def check(self):
        if self.problem:
            raise self.problem
        if self.cancelled.is_set():
            raise BackgroundError("后台请求已取消")
        if time.monotonic() >= self.deadline:
            raise BackgroundError("后台读取超时")
        if not self.backend.identity_ok():
            raise BackgroundError("远航版进程或窗口已改变，请重新连接")
        if self.activation_guard is not None:
            self.activation_guard.check()
        self.sample()

    def start(self):
        if self.source == self.backend.target_desktop_id:
            raise BackgroundError("正在同花顺桌面操作时，后台读取暂停")
        self.check()

        def observe():
            import pythoncom

            pythoncom.CoInitialize()
            try:
                while not self.stopping.is_set():
                    try:
                        self.sample()
                    except Exception as exc:
                        self.problem = BackgroundInterference(str(exc))
                    finally:
                        self.ready.set()
                    if self.problem or self.stopping.wait(0.02):
                        return
            finally:
                pythoncom.CoUninitialize()

        self.thread = threading.Thread(target=observe, daemon=True)
        self.thread.start()
        if not self.ready.wait(1):
            self.stopping.set()
            raise BackgroundError("前台监测未能启动，已取消后台读取")
        self.check()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=1)


@contextmanager
def query_lease(pid):
    """Lease released by Windows if a previous reader crashed."""
    import win32event
    import win32api

    mutex = win32event.CreateMutex(
        None, False, "Local\\FrytraderThsBackground-%s" % pid
    )
    acquired = False
    try:
        result = win32event.WaitForSingleObject(mutex, 0)
        if result not in (win32event.WAIT_OBJECT_0, 0x80):
            raise BackgroundError("另一个后台读取器正在使用同花顺")
        acquired = True
        yield
    finally:
        if acquired:
            win32event.ReleaseMutex(mutex)
        win32api.CloseHandle(mutex)


def parse_grid(raw, kind):
    required = PAGES[kind][1]
    if (
        not isinstance(raw, str)
        or not raw
        or any(
            field not in raw.splitlines()[0].split("\t") for field in required
        )
    ):
        raise BackgroundError("表格列名与查询页不符，拒绝返回陈旧数据")
    try:
        frame = pd.read_csv(
            io.StringIO(raw),
            delimiter="\t",
            na_filter=False,
            dtype=CommonConfig.GRID_DTYPE,
        )
        trailing = [
            col
            for col in frame.columns
            if str(col).startswith("Unnamed:") and (frame[col] == "").all()
        ]
        return _drop_empty_rows(frame.drop(columns=trailing))
    except Exception as exc:
        raise BackgroundError("后台表格无法解析") from exc


class _Engine:
    def __init__(self, options, backend=None, runtime=None, focus=None):
        self.options = options
        self.backend = backend
        self.runtime = runtime
        self.focus = focus
        self.connected = False
        self.owned_captcha = None
        self.last_report = {}

    def connect(self, exe_path=None):
        if self.connected:
            return
        # Validate dependencies before moving any window.
        if self.runtime is None:
            self.runtime = AutoHotkeyControls(self.options.ahk_path)
        if self.focus is None:
            from easytrader.background_focus import ThreadActivationGuard

            self.focus = ThreadActivationGuard()
        if self.backend is None:
            from easytrader.background_windows import WindowsBackend

            self.backend = WindowsBackend()
        try:
            self.backend.discover(exe_path)
            self.backend.validate_activation_guard()
            if self.backend.captcha():
                raise BackgroundError(
                    "客户端已有验证码，请先处理后连接后台模式"
                )
            self.backend.enter_background(self.options.desktop_name)
            self.connected = True
        except Exception:
            self.backend.restore()
            raise

    def _read_result(self, sequence, kind):
        if self.backend.clipboard_sequence() == sequence:
            return None
        if self.backend.clipboard_owner_pid() != self.backend.trader_pid:
            raise BackgroundError("剪贴板被其他程序接管，已停止读取")
        raw = self.backend.clipboard_text()
        if not raw:
            return None
        # Do not accept header-only/stale data from another query page.
        parse_grid(raw, kind)
        return raw

    def _captcha_image(self, cap, guard, settle=0.1):
        from easytrader.screenshot import CaptureError

        self.backend.protect_captcha(cap)
        settle_deadline = time.monotonic() + settle
        while time.monotonic() < settle_deadline:
            guard.check()
            time.sleep(self.options.poll_interval)
        deadline = min(time.monotonic() + 1, guard.deadline)
        failure = None
        while time.monotonic() < deadline:
            guard.check()
            if not self.backend.check_captcha(cap):
                raise BackgroundError("验证码控件在绘图期间改变")
            try:
                image = self.backend.capture_captcha(cap.image)
                if all(high - low < 8 for low, high in image.getextrema()):
                    raise CaptureError("验证码绘图尚未完成")
                guard.check()
                return image
            except CaptureError as exc:
                failure = exc
                time.sleep(self.options.poll_interval)
        raise BackgroundError("验证码绘图在期限内未就绪") from failure

    def _finish_copy(self, sequence, kind, guard):
        attempts = 0
        options = self.options
        while True:
            guard.check()
            cap = self.backend.captcha()
            if cap is None:
                raw = self._read_result(sequence, kind)
                if raw is not None:
                    self.last_report["captcha_attempts"] = attempts
                    return parse_grid(raw, kind)
                time.sleep(options.poll_interval)
                continue
            if not self.backend.check_captcha(cap):
                raise BackgroundError("验证码控件已改变")
            self.owned_captcha = cap
            if attempts >= options.captcha_retries:
                raise BackgroundError("验证码未通过，未使用任何前台输入回退")
            attempts += 1
            self.last_report["phase"] = "capture_captcha"
            image = self._captcha_image(cap, guard)
            extrema = image.convert("RGB").getextrema()
            if all(high - low < 8 for low, high in extrema):
                raise BackgroundError("验证码截图为空，未激活或显示窗口")
            before = image.tobytes()
            import tempfile

            with tempfile.TemporaryDirectory(
                prefix="frytrader-captcha-"
            ) as folder:
                path = Path(folder) / "captcha.png"
                image.save(path)
                self.last_report["phase"] = "recognize_captcha"
                code = captcha_recognize_improved(
                    str(path), tesseract_path=options.tesseract_path
                )
            guard.check()
            if code:
                self.last_report["phase"] = "fill_captcha"
                self.runtime.fill(cap, code, self.backend.trader_pid, guard)
                guard.check()
                if not self.backend.check_captcha(cap):
                    raise BackgroundError("验证码控件在输入期间改变")
                self.last_report["phase"] = "submit_captcha"
                self.runtime.click(cap, "ok", self.backend.trader_pid, guard)
                outcome_deadline = min(time.monotonic() + 1, guard.deadline)
                while time.monotonic() < outcome_deadline:
                    guard.check()
                    if self.backend.captcha() is None:
                        raw = self._read_result(sequence, kind)
                        if raw is not None:
                            self.owned_captcha = None
                            self.last_report["captcha_attempts"] = attempts
                            return parse_grid(raw, kind)
                    time.sleep(options.poll_interval)
            guard.check()
            cap = self.backend.captcha()
            if cap is None:
                continue
            self.runtime.click(cap, "image", self.backend.trader_pid, guard)
            refresh_deadline = min(time.monotonic() + 1, guard.deadline)
            changed = False
            while time.monotonic() < refresh_deadline:
                guard.check()
                if (
                    self._captcha_image(cap, guard, settle=0).tobytes()
                    != before
                ):
                    changed = True
                    break
                time.sleep(options.poll_interval)
            if not changed:
                raise BackgroundError("验证码图片未刷新，已停止重复提交")

    def query(self, kind, deadline, cancelled):
        if not self.connected:
            raise BackgroundError("请先 connect() 到已登录的远航版")
        guard = QueryGuard(self.backend, deadline, cancelled)
        started = time.monotonic()
        self.last_report = {"query": kind, "captcha_attempts": 0}
        try:
            with query_lease(self.backend.trader_pid):
                guard.start()
                self.focus.start(self.backend, guard)
                guard.activation_guard = self.focus
                self.backend.begin_read()
                self.backend.ensure_render_ready()
                guard.check()
                if self.backend.captcha():
                    raise BackgroundError("已有待处理验证码，未自动关闭")
                leaf = "资金股票" if kind == "balance" else PAGES[kind][0]
                self.last_report["phase"] = "select_page"
                self.backend.select_page(["查询[F4]", leaf])
                settle_deadline = time.monotonic() + self.options.page_settle
                self.last_report["phase"] = "settle_page"
                while time.monotonic() < settle_deadline:
                    guard.check()
                    time.sleep(self.options.poll_interval)
                guard.check()
                if kind == "balance":
                    self.last_report["phase"] = "read_balance"
                    result = self.backend.balance()
                else:
                    grid = self.backend.grid()
                    sequence = self.backend.clipboard_sequence()
                    self.last_report["phase"] = "request_copy"
                    self.backend.copy_grid(grid)
                    self.last_report["phase"] = "wait_result"
                    result = self._finish_copy(sequence, kind, guard)
                guard.check()
                self.last_report["ok"] = True
                return result
        except Exception as exc:
            self.last_report.update(ok=False, error=type(exc).__name__)
            if isinstance(exc, BackgroundError):
                raise
            raise BackgroundError(str(exc)) from exc
        finally:
            self.last_report["seconds"] = round(time.monotonic() - started, 3)
            try:
                self.backend.end_read()
            finally:
                try:
                    guard.activation_guard = None
                    if self.focus is not None:
                        self.focus.close()
                finally:
                    guard.close()

    def restore_windows(self):
        """Restore placement only on an explicit request."""
        if self.backend is not None:
            self.backend.restore()
        self.connected = False

    def close(self):
        if self.options.restore_on_close:
            self.restore_windows()
        self.connected = False
        self.owned_captcha = None


class BackgroundThsTrader:
    """Readonly background mode for the existing Voyager login session.

    close() stops the executor and leaves Voyager on its background desktop.
    Call restore_windows() explicitly when window placement should be restored.
    Windows Task View is window grouping, not a separate keyboard environment;
    therefore this adapter never falls back to actual keyboard/mouse input.
    """

    def __init__(self, **options):
        self.options = BackgroundOptions(**options)
        self._queue = queue.Queue()
        self._thread = None
        self._connected = False
        self._lifecycle_lock = threading.RLock()

    def _start(self):
        if self._thread is not None:
            return
        if __import__("os").name != "nt":
            raise BackgroundError("同花顺后台模式仅支持 Windows")

        def worker():
            import pythoncom

            pythoncom.CoInitialize()
            engine = _Engine(self.options)
            try:
                while True:
                    item = self._queue.get()
                    if item is None:
                        return
                    future, command, args, deadline, cancelled = item
                    if not future.set_running_or_notify_cancel():
                        continue
                    try:
                        if cancelled.is_set():
                            raise BackgroundError("后台请求已取消")
                        if command == "connect":
                            result = engine.connect(*args)
                        elif command == "close":
                            result = engine.close()
                        elif command == "restore_windows":
                            result = engine.restore_windows()
                        elif command == "report":
                            result = dict(engine.last_report)
                        else:
                            result = engine.query(command, deadline, cancelled)
                        future.set_result(result)
                    except Exception as exc:
                        future.set_exception(exc)
            finally:
                try:
                    engine.close()
                finally:
                    pythoncom.CoUninitialize()

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def _call(self, command, *args):
        with self._lifecycle_lock:
            self._start()
            future = Future()
            cancelled = threading.Event()
            deadline = time.monotonic() + self.options.timeout
            self._queue.put((future, command, args, deadline, cancelled))
            try:
                return future.result(timeout=self.options.timeout + 3)
            except FutureTimeout as exc:
                cancelled.set()
                future.cancel()
                raise BackgroundError(
                    "后台工作线程超时，请 close() 后重新连接"
                ) from exc

    def connect(self, exe_path=None):
        with self._lifecycle_lock:
            self._call("connect", exe_path)
            self._connected = True
        return self

    @property
    def balance(self):
        return self._call("balance")

    @property
    def position(self):
        return self._call("position")

    @property
    def today_entrusts(self):
        return self._call("today_entrusts")

    @property
    def today_trades(self):
        return self._call("today_trades")

    @property
    def last_report(self):
        return self._call("report")

    def restore_windows(self):
        with self._lifecycle_lock:
            self._call("restore_windows")
            self._connected = False

    def close(self):
        with self._lifecycle_lock:
            if self._thread is None:
                return
            thread = self._thread
            try:
                self._call("close")
            finally:
                self._queue.put(None)
                thread.join(timeout=2)
                if not thread.is_alive():
                    self._thread = None
                    self._connected = False
                atexit.unregister(self.close)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def _readonly(self, *args, **kwargs):
        raise PermissionError("后台模式只支持查询，不支持登录、下单或撤单")

    buy = sell = market_buy = market_sell = trade = _readonly
    market_trade = cancel_entrust = cancel_all_entrusts = _readonly
    auto_ipo = repo = reverse_repo = prepare = login = exit = _readonly
