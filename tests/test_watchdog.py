from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import core


class TestWatchdog(unittest.TestCase):
    def monitor(self, samples, child_cpu=0):
        proc = Mock()
        proc.is_running.return_value = True
        proc.status.return_value = "running"
        proc.cpu_percent.side_effect = [0] + samples
        child = Mock()
        child.cpu_percent.return_value = child_cpu
        proc.children.return_value = [child]
        stop = Mock()
        stop.wait.side_effect = [False] * len(samples) + [True]
        psutil = SimpleNamespace(Process=Mock(return_value=proc),
                                 NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
                                 AccessDenied=type("AccessDenied", (Exception,), {}),
                                 STATUS_ZOMBIE="zombie")
        with patch.dict(sys.modules, {"psutil": psutil}), \
             patch.object(config, "WATCHDOG_HANG_THRESHOLD", 2), \
             patch.object(core.logging, "warning") as warning:
            core._watchdog_monitor(123, stop)
        proc.kill.assert_not_called()
        proc.terminate.assert_not_called()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
        return warning.call_count

    def test_long_idle_only_warns_once_and_does_not_kill(self):
        self.assertEqual(self.monitor([0] * 10), 1)

    def test_activity_resets_warning_period(self):
        self.assertEqual(self.monitor([0, 0, 0, 5, 0, 0, 0]), 2)

    def test_active_child_prevents_idle_warning(self):
        self.assertEqual(self.monitor([0] * 5, child_cpu=5), 0)

    def test_disappearing_or_inaccessible_process_is_not_killed(self):
        for error_name in ("NoSuchProcess", "AccessDenied"):
            with self.subTest(error_name=error_name):
                errors = {name: type(name, (Exception,), {})
                          for name in ("NoSuchProcess", "AccessDenied")}
                proc = Mock()
                proc.cpu_percent.return_value = 0
                proc.is_running.side_effect = errors[error_name]()
                psutil = SimpleNamespace(Process=Mock(return_value=proc),
                                         STATUS_ZOMBIE="zombie", **errors)
                stop = Mock()
                stop.wait.return_value = False
                with patch.dict(sys.modules, {"psutil": psutil}):
                    core._watchdog_monitor(123, stop)
                proc.kill.assert_not_called()

    def test_completed_installer_returns_success_with_monitor_enabled(self):
        worker = core.InstallWorker([], lambda msg: None)
        proc = Mock(pid=123, returncode=0)
        with patch.object(config, "WATCHDOG_ENABLED", True), \
             patch.object(core.subprocess, "Popen", return_value=proc), \
             patch.object(core.threading, "Thread") as thread:
            self.assertEqual(worker._spawn_process(["setup.exe"], "setup.exe", 900), 0)
        proc.kill.assert_not_called()
        thread.return_value.start.assert_called_once()
        thread.return_value.join.assert_called_once()
        self.assertFalse(worker._active_procs)

    def test_explicit_install_timeout_still_terminates_process(self):
        worker = core.InstallWorker([], lambda msg: None)
        proc = Mock(pid=123)
        proc.wait.side_effect = [subprocess.TimeoutExpired(["setup.exe"], 900), 0]
        with patch.object(config, "WATCHDOG_ENABLED", False), \
             patch.object(core.subprocess, "Popen", return_value=proc):
            with self.assertRaises(subprocess.TimeoutExpired):
                worker._spawn_process(["setup.exe"], "setup.exe", 900)
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)
        self.assertFalse(worker._active_procs)

    def test_user_cancel_still_terminates_active_process(self):
        worker = core.InstallWorker([], lambda msg: None)
        proc = Mock()
        worker._active_procs.add(proc)
        worker.stop()
        proc.terminate.assert_called_once()
        self.assertFalse(worker._is_running)
