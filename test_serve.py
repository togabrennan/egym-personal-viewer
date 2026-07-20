import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
