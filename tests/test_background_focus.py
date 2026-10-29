"""Offline integrity, identity and helper-lifetime tests (no client hooks)."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from easytrader.background_focus import ThreadActivationGuard, pe_machine
from easytrader.background_runtime import BackgroundError


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.guard = SimpleNamespace(
            deadline=time.monotonic() + 10, check=mock.Mock()
        )
        self.backend = SimpleNamespace(hook_target=lambda: (123, 456, 789))
        self.process = mock.Mock()
        self.process.returncode = None
        self.process.poll.return_value = None
        self.environment = None
        self.force_timeout = False

    def spawn(self, command, **kwargs):
        self.environment = kwargs["env"]
        folder = Path(self.environment["FRYTRADER_FOCUS_DIRECTORY"])
        (folder / "ready.txt").write_text("ok", encoding="ascii")
        return self.process

    def normal_wait(self, timeout):
        folder = Path(self.environment["FRYTRADER_FOCUS_DIRECTORY"])
        self.assertTrue((folder / "stop.txt").is_file())
        self.process.returncode = 0
        self.process.poll.return_value = 0
        return 0

    def test_bundled_runtime_is_x86_and_passes_hash_validation(self):
        runtime = ThreadActivationGuard()
        self.assertEqual(
            pe_machine(runtime.directory / "focus_guard32.dll"), 0x14C
        )
        self.assertEqual(
            pe_machine(runtime.directory / "focus_host32.exe"), 0x14C
        )
        self.assertIsNone(runtime.host)

    def test_corrupted_runtime_is_rejected_before_starting_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "focus_guard32.dll").write_bytes(b"changed")
            with self.assertRaisesRegex(BackgroundError, "校验"):
                ThreadActivationGuard(directory)

    def test_non_pe_file_is_not_a_supported_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "not.exe")
            path.write_bytes(b"not a PE")
            with self.assertRaises(BackgroundError):
                pe_machine(path)

    def test_private_target_identity_and_parent_pid_are_exact(self):
        runtime = ThreadActivationGuard()
        self.process.wait.side_effect = self.normal_wait
        with mock.patch(
            "easytrader.background_focus.subprocess.Popen",
            side_effect=self.spawn,
        ) as launch:
            runtime.start(self.backend, self.guard)
            folder = runtime.folder
            values = (folder / "target.txt").read_text(encoding="ascii")
            self.assertEqual(values, "123,0,456,789,%d" % os.getpid())
            self.assertFalse(launch.call_args.kwargs["shell"])
            self.assertNotIn("password", str(launch.call_args))
            runtime.close()
            self.assertFalse(folder.exists())
        self.process.terminate.assert_not_called()

    def test_dead_host_blocks_any_further_operation(self):
        runtime = ThreadActivationGuard()
        with self.assertRaises(BackgroundError):
            runtime.check()
        self.process.wait.side_effect = self.normal_wait
        with mock.patch(
            "easytrader.background_focus.subprocess.Popen",
            side_effect=self.spawn,
        ):
            runtime.start(self.backend, self.guard)
            self.process.poll.return_value = 8
            self.process.returncode = 8
            with self.assertRaisesRegex(BackgroundError, "已退出"):
                runtime.check()
            with self.assertRaises(BackgroundError):
                runtime.close()
        self.assertIsNone(runtime.host)
        self.assertIsNone(runtime.temp)

    def test_close_without_start_is_noop_and_does_not_touch_client(self):
        runtime = ThreadActivationGuard()
        runtime.close()
        runtime.close()
        self.assertIsNone(runtime.host)

    def test_guard_failure_after_install_still_stops_owned_helper(self):
        runtime = ThreadActivationGuard()
        self.process.wait.side_effect = self.normal_wait
        checks = [None, BackgroundError("cancelled")]
        self.guard.check.side_effect = checks
        with mock.patch(
            "easytrader.background_focus.subprocess.Popen",
            side_effect=self.spawn,
        ):
            with self.assertRaisesRegex(BackgroundError, "cancelled"):
                runtime.start(self.backend, self.guard)
        self.assertIsNone(runtime.host)
        self.assertIsNone(runtime.temp)
        self.process.wait.assert_called_once()

    def test_parent_environment_is_not_mutated(self):
        runtime = ThreadActivationGuard()
        before = dict(os.environ)
        self.process.wait.side_effect = self.normal_wait
        with mock.patch(
            "easytrader.background_focus.subprocess.Popen",
            side_effect=self.spawn,
        ):
            runtime.start(self.backend, self.guard)
            runtime.close()
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
