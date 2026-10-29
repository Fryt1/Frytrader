"""Double-click desktop controller; no trading or global simulated input.

The controller keeps the original HDESK open. A separate return-panel process
starts on the target desktop before entry is allowed. Both processes watch the
return deadline. Closing this controller never closes Tonghuashun.
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.win_desktop import (  # noqa: E402 - direct-file entry point
    DesktopAPI,
    return_to_origin,
    validate_name,
)

TARGET = "FrytraderPrivate"
RETURN_TITLE = "Frytrader - 返回工作桌面"
VOYAGER = Path(r"C:\同花顺远航版\bin\happ.exe")


def write_state(path, value):
    temporary = path.with_suffix(".new")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(str(temporary), str(path))


def read_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class ReturnGuard:
    """A deadline independent of the GUI thread, with a known return target."""

    def __init__(self, api, origin, target):
        self.api, self.origin, self.target = api, origin, target
        self.deadline = None
        self.seen_target = False
        self.error = ""
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None

    def arm(self, seconds):
        if not 10 <= seconds <= 3600:
            raise ValueError("自动返回时间应在 10 秒至 60 分钟之间。")
        with self.lock:
            self.deadline = time.monotonic() + seconds
            self.seen_target = False
            self.error = ""

    def disarm(self):
        with self.lock:
            self.deadline = None

    def remaining(self):
        with self.lock:
            if self.deadline is None:
                return None
            return max(0, int(self.deadline - time.monotonic()))

    def return_now(self):
        return_to_origin(self.api, self.origin, self.target)
        self.disarm()
        self.error = ""

    def step(self, now=None, helper_alive=True):
        now = time.monotonic() if now is None else now
        with self.lock:
            deadline = self.deadline
            if deadline is None:
                return
            current = self.api.input_name()
            if current == self.target:
                self.seen_target = True
            elif current == self.origin.name and self.seen_target:
                self.deadline = None
                return
            due = now >= deadline or not helper_alive
        if due:
            try:
                self.return_now()
            except (OSError, RuntimeError) as exc:
                # Keep the deadline armed while Windows is locked or in UAC.
                self.error = str(exc)

    def start(self, child_alive=lambda: True):
        def monitor():
            while not self.stopping.wait(0.15):
                try:
                    self.step(helper_alive=child_alive())
                except (OSError, RuntimeError) as exc:
                    self.error = str(exc)

        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=1)


class Session:
    def __init__(self, api, origin_name=None, target_name=TARGET):
        self.api = api
        name = origin_name or api.thread_name()
        if name == target_name:
            raise RuntimeError("请从工作桌面打开控制面板。")
        self.origin = api.open(name)
        try:
            self.target = api.open(target_name, create=True)
        except Exception:
            self.origin.close()
            raise
        self.temp = tempfile.TemporaryDirectory(prefix="frytrader-desktop-")
        self.directory = Path(self.temp.name)
        self.child = None
        self.guard = ReturnGuard(api, self.origin, target_name)

    def prepare_helper(self, probe=False):
        if self.child and self.child.alive():
            return
        if self.child:
            self.child.close()
        args = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--helper",
            "--origin",
            self.origin.name,
            "--target",
            self.target.name,
            "--session",
            str(self.directory),
        ]
        if probe:
            args.append("--probe")
        self.child = self.api.start(args, self.target, ROOT)

    def helper_ready(self):
        state = read_state(self.directory / "ready.json")
        if not self.child or not self.child.alive():
            return False
        if (
            state.get("pid") != self.child.pid
            or state.get("desktop") != self.target.name
            or state.get("origin") != self.origin.name
        ):
            return False
        # A file alone is insufficient: verify the return window exists on
        # the intended desktop and belongs to our exact helper process.
        return any(
            w["pid"] == self.child.pid and w["title"] == RETURN_TITLE
            for w in self.api.windows(self.target)
        )

    def command(self, action, **kwargs):
        write_state(
            self.directory / "command.json",
            dict(action=action, token=uuid.uuid4().hex, **kwargs),
        )

    def enter(self, seconds):
        if self.api.input_name() != self.origin.name:
            raise RuntimeError("请先返回工作桌面，并关闭锁屏或 UAC 提示。")
        if not self.helper_ready():
            raise RuntimeError("返回按钮尚未就绪，已取消切换。")
        self.guard.arm(seconds)
        try:
            self.command("arm", seconds=seconds)
            self.api.switch(self.target)
        except Exception:
            self.guard.disarm()
            try:
                self.command("disarm")
            except OSError:
                pass
            raise

    def start_voyager(self, executable):
        """Launch the existing Voyager only after an explicit UI action."""
        executable = Path(executable).resolve()
        if executable.name.lower() != "happ.exe" or not executable.is_file():
            raise ValueError("请选择现有远航版的 bin\\happ.exe。")
        target_pids = {w["pid"] for w in self.api.windows(self.target)}
        running = self.api.trader_processes()
        if any(pid not in target_pids for pid, _ in running):
            raise RuntimeError(
                "远航版已在工作桌面运行；现有登录已保留。\n"
                "如需换到独立桌面，请先自行退出，再点这里启动。"
            )
        if running:
            return "远航版已经在独立桌面运行。"
        process = self.api.start(
            [str(executable)], self.target, executable.parent
        )
        pid = process.pid
        process.close()  # Release the handle, NOT the application.
        return "已启动远航版（PID %s），请点“进入同花顺桌面”登录。" % pid

    def close(self):
        # Do not remove the return panel until the user is safely back.
        self.guard.return_now()
        self.guard.close()
        if self.child and self.child.alive():
            self.command("stop")
            deadline = time.monotonic() + 2
            while self.child.alive() and time.monotonic() < deadline:
                time.sleep(0.03)
            if self.child.alive():
                # Only terminate our exact return-panel child, never traders.
                self.child.terminate_helper()
                time.sleep(0.1)
        if self.child:
            self.child.close()
        self.temp.cleanup()
        self.target.close()
        self.origin.close()


def helper(args):
    # CreateProcess set lpDesktop before Python/Tk started; no late
    # SetThreadDesktop call and no imports of the trader GUI package here.
    import tkinter as tk
    from tkinter import ttk

    api = DesktopAPI()
    directory = Path(args.session)
    if api.thread_name() != args.target:
        raise RuntimeError("Return helper started on the wrong desktop")
    origin = api.open(args.origin)
    guard = ReturnGuard(api, origin, args.target)
    guard.start()
    root = tk.Tk()
    root.title(RETURN_TITLE)
    root.attributes("-topmost", True)
    root.geometry("390x185+24+24")
    root.minsize(340, 175)
    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)
    status = tk.StringVar(value="这里是同花顺独立桌面")
    ttk.Label(frame, textvariable=status).pack(anchor="w", pady=(0, 8))

    def go_back():
        try:
            guard.return_now()
            status.set("已返回工作桌面，下次进入仍可使用此按钮。")
        except (RuntimeError, OSError) as exc:
            status.set(str(exc))

    ttk.Button(frame, text="返回工作桌面", command=go_back).pack(
        fill="x", ipady=10
    )
    note = tk.StringVar(value="此面板保持置顶；关闭按钮也会返回。")
    ttk.Label(frame, textvariable=note).pack(anchor="w", pady=(10, 0))
    root.protocol("WM_DELETE_WINDOW", go_back)
    root.update_idletasks()
    write_state(
        directory / "ready.json",
        {
            "pid": os.getpid(),
            "desktop": api.thread_name(),
            "origin": args.origin,
        },
    )
    last_token = [None]

    def poll():
        command = read_state(directory / "command.json")
        if command.get("token") != last_token[0]:
            last_token[0] = command.get("token")
            action = command.get("action")
            if action == "arm":
                guard.arm(int(command["seconds"]))
            elif action == "disarm":
                guard.disarm()
            elif action == "stop":
                try:
                    guard.return_now()
                except (OSError, RuntimeError) as exc:
                    status.set(str(exc))
                else:
                    root.destroy()
                    return
        remaining = guard.remaining()
        if remaining is not None:
            note.set("安全返回倒计时：%s 秒" % remaining)
        elif guard.error:
            note.set("请先解锁 Windows；解锁后自动重试返回。")
        else:
            note.set("切换不会退出同花顺；关闭按钮也会返回。")
        root.after(100, poll)

    if args.probe:
        # Readiness-only smoke test; no switch and no keyboard/mouse events.
        def probe():
            write_state(
                directory / "probe.json",
                {
                    "desktop": api.thread_name(),
                    "input_desktop": api.input_name(),
                    "return_button": "返回工作桌面",
                    "topmost": bool(root.attributes("-topmost")),
                    "pid": os.getpid(),
                },
            )

        root.after(300, probe)
    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        guard.close()
        origin.close()


def panel():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    api = DesktopAPI()
    mutex = api.singleton()
    root = tk.Tk()
    root.title("Frytrader · 同花顺桌面控制")
    root.geometry("510x385")
    root.minsize(480, 365)
    session = Session(api)
    session.guard.start(lambda: bool(session.child and session.child.alive()))
    body = ttk.Frame(root, padding=18)
    body.pack(fill="both", expand=True)
    ttk.Label(
        body, text="远航版独立桌面", font=("Microsoft YaHei UI", 15)
    ).pack(anchor="w")
    ttk.Label(
        body,
        text=(
            "登录时进入，完成后点独立桌面左上角的“返回工作桌面”。\n"
            "切换不会关闭同花顺；券商超时或断线仍可能需要重新登录。"
        ),
    ).pack(anchor="w", pady=(8, 12))
    status = tk.StringVar(value="正在准备独立桌面的返回按钮……")
    ttk.Label(body, textvariable=status, wraplength=465).pack(
        anchor="w", pady=(0, 12)
    )
    row = ttk.Frame(body)
    row.pack(fill="x")
    seconds = tk.IntVar(value=180)
    ttk.Label(row, text="最长停留（秒）：").pack(side="left")
    ttk.Spinbox(row, from_=10, to=3600, textvariable=seconds, width=7).pack(
        side="left"
    )
    ttk.Label(row, text="到时自动返回").pack(side="left", padx=8)
    buttons = ttk.Frame(body)
    buttons.pack(fill="x", pady=(16, 10))

    def action(fn):
        try:
            fn()
        except Exception as exc:
            status.set(str(exc))
            messagebox.showerror("操作未完成", str(exc), parent=root)

    enter_button = ttk.Button(
        buttons,
        text="进入同花顺桌面",
        state="disabled",
        command=lambda: action(lambda: session.enter(seconds.get())),
    )
    enter_button.grid(row=0, column=0, sticky="ew", padx=(0, 5), ipady=7)
    ttk.Button(
        buttons,
        text="返回工作桌面",
        command=lambda: action(session.guard.return_now),
    ).grid(row=0, column=1, sticky="ew", padx=(5, 0), ipady=7)

    def launch():
        executable = VOYAGER
        if not executable.is_file():
            selected = filedialog.askopenfilename(
                title="选择现有远航版 bin\\happ.exe",
                filetypes=[("远航版主程序", "happ.exe")],
                parent=root,
            )
            if not selected:
                return
            executable = Path(selected)
        status.set(session.start_voyager(executable))

    ttk.Button(
        buttons, text="在独立桌面启动远航版", command=lambda: action(launch)
    ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0), ipady=6)
    buttons.columnconfigure(0, weight=1)
    buttons.columnconfigure(1, weight=1)
    ttk.Label(
        body,
        text=(
            "当前工作桌面的登录实例会保留，不会自动关闭或搬移。\n"
            "此入口仅管理桌面；完整后台查询还需单独验证。"
        ),
        foreground="#555555",
    ).pack(anchor="w", pady=(6, 0))

    preparing = [True]
    prepare_deadline = [time.monotonic() + 10]
    session.prepare_helper()

    def poll():
        try:
            ready = session.helper_ready()
            enter_button.configure(state="normal" if ready else "disabled")
            if preparing[0] and ready:
                preparing[0] = False
                status.set("返回按钮已就绪，可以一键进入和返回。")
            elif preparing[0] and time.monotonic() > prepare_deadline[0]:
                preparing[0] = False
                status.set("返回按钮未能启动，禁止切换；请重开控制面板。")
            if session.guard.error:
                status.set("Windows 已锁屏或处于安全界面；解锁后重试。")
        except Exception as exc:
            status.set(str(exc))
        root.after(400, poll)

    def close():
        try:
            session.close()
        except Exception as exc:
            messagebox.showerror("请先返回工作桌面", str(exc), parent=root)
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        api.kernel.CloseHandle(mutex)


def smoke_test():
    """Create and inspect the return UI without switching the input desktop."""
    api = DesktopAPI()
    before = api.input_name()
    session = Session(api, target_name="FrytraderTest_" + uuid.uuid4().hex[:8])
    try:
        session.prepare_helper(probe=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if session.helper_ready() and read_state(
                session.directory / "probe.json"
            ):
                break
            if not session.child.alive():
                raise RuntimeError("Return helper exited before readiness")
            time.sleep(0.05)
        else:
            raise TimeoutError("Return panel readiness timed out")
        result = read_state(session.directory / "probe.json")
        result["ready_verified"] = session.helper_ready()
        result["visible_on_work_desktop"] = any(
            w["pid"] == session.child.pid for w in api.windows(session.origin)
        )
        result["input_desktop_before"] = before
        result["input_desktop_after"] = api.input_name()
        if result["visible_on_work_desktop"]:
            raise RuntimeError("Return panel leaked onto work desktop")
        if result["input_desktop_after"] != before:
            raise RuntimeError("Input desktop unexpectedly changed")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--origin")
    parser.add_argument("--target", default=TARGET, type=validate_name)
    parser.add_argument("--session")
    args = parser.parse_args()
    if args.helper:
        if not args.origin or not args.session:
            parser.error("--helper requires --origin and --session")
        helper(args)
    elif args.self_test:
        smoke_test()
    else:
        try:
            panel()
        except Exception as exc:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Frytrader 桌面控制", str(exc), parent=root)
            root.destroy()


if __name__ == "__main__":
    main()
