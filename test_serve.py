from __future__ import annotations

import errno
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import serve


class ServeRefetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data = serve.DATA
        serve.DATA = Path(self.tmp.name) / "workouts.json"
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        serve.DATA = self.old_data
        self.tmp.cleanup()

    def write_data(self, generated_at: str, rows: list[dict] | None = None) -> None:
        serve.DATA.write_text(json.dumps({
            "generated_at": generated_at,
            "rows": rows or [],
        }))

    def test_no_today_exercise_refetches_when_last_fetch_is_old(self) -> None:
        self.write_data("2026-07-20T09:30:00+00:00")

        should_fetch, reason = serve.should_refetch(self.now)

        self.assertTrue(should_fetch)
        self.assertIn("no exercise recorded today yet", reason)

    def test_no_today_exercise_waits_when_last_fetch_is_recent(self) -> None:
        self.write_data("2026-07-20T11:30:00+00:00")

        should_fetch, reason = serve.should_refetch(self.now)

        self.assertFalse(should_fetch)
        self.assertIn("last fetch was only 30m ago", reason)

    def test_today_workout_waits_when_retry_interval_has_not_elapsed(self) -> None:
        self.write_data("2026-07-20T11:30:00+00:00", [{
            "exercise_completed_at": "2026-07-20T11:20:00+00:00",
        }])

        should_fetch, reason = serve.should_refetch(self.now)

        self.assertFalse(should_fetch)
        self.assertIn("last fetch was only 30m ago", reason)

    def test_today_workout_refetches_after_retry_interval(self) -> None:
        self.write_data("2026-07-20T09:30:00+00:00", [{
            "exercise_completed_at": "2026-07-20T09:20:00+00:00",
        }])

        should_fetch, reason = serve.should_refetch(self.now)

        self.assertTrue(should_fetch)
        self.assertIn("workout may still be syncing", reason)

    def test_today_workout_complete_when_fetch_was_after_full_interval(self) -> None:
        self.write_data("2026-07-20T11:30:00+00:00", [{
            "exercise_completed_at": "2026-07-20T09:20:00+00:00",
        }])

        should_fetch, reason = serve.should_refetch(self.now)

        self.assertFalse(should_fetch)
        self.assertIn("today's workout looks complete", reason)

    def test_startup_checks_retry_logic_after_fresh_date_check(self) -> None:
        self.write_data("2026-07-20T09:30:00+00:00")

        should_fetch, reason = serve.should_fetch_on_start(now=self.now)

        self.assertTrue(should_fetch)
        self.assertIn("no exercise recorded today yet", reason)


    def test_fetch_skips_lock_held_by_another_process(self) -> None:
        code = '''
import pathlib, sys
from unittest.mock import patch
import serve
serve.DATA = pathlib.Path(sys.argv[1])
with patch.object(serve.subprocess, "run") as fetch:
    assert serve.run_fetch() == "A fetch is already running"
    fetch.assert_not_called()
'''
        with (serve.DATA.parent / ".fetch.lock").open("a+b") as lock_file:
            serve._fetch_file_lock(lock_file)
            result = subprocess.run([sys.executable, "-c", code, str(serve.DATA)],
                                    cwd=serve.HERE, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("another process", result.stderr)

    def test_fetch_reports_failure_and_releases_file_lock(self) -> None:
        with patch.object(serve.subprocess, "run") as fetch:
            fetch.return_value.returncode = 1
            self.assertIn("exit code 1", serve.run_fetch())
            fetch.return_value.returncode = 0
            self.assertIsNone(serve.run_fetch())
            self.assertEqual(fetch.call_count, 2)
        with (serve.DATA.parent / ".fetch.lock").open("a+b") as lock_file:
            serve._fetch_file_lock(lock_file)

    def test_windows_fetch_lock_and_contention(self) -> None:
        windows = Mock(LK_NBLCK=2, LK_UNLCK=0)
        with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": windows}):
            with patch.object(serve.subprocess, "run") as fetch:
                fetch.return_value.returncode = 0
                self.assertIsNone(serve.run_fetch())
                self.assertEqual([call.args[1:] for call in windows.locking.call_args_list],
                                 [(windows.LK_NBLCK, 1), (windows.LK_UNLCK, 1)])
                windows.locking.reset_mock()
                windows.locking.side_effect = OSError(errno.EACCES, "Lock held")
                fetch.reset_mock()
                self.assertEqual(serve.run_fetch(), "A fetch is already running")
                fetch.assert_not_called()
                self.assertEqual(windows.locking.call_count, 1)

    def test_windows_lock_io_errors_are_not_reported_as_busy(self) -> None:
        windows = Mock(LK_NBLCK=2, LK_UNLCK=0)
        windows.locking.side_effect = OSError(errno.EBADF, "Bad descriptor")
        with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": windows}):
            with self.assertRaises(OSError) as raised:
                serve.run_fetch()
            self.assertNotIsInstance(raised.exception, BlockingIOError)


if __name__ == "__main__":
    unittest.main()
