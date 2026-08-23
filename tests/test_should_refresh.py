import unittest
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import v2.should_refresh as refresh
from v2.should_refresh import decide_mode


UTC = timezone.utc


class RefreshModeTests(unittest.TestCase):
    def test_full_window_wins(self):
        self.assertEqual(decide_mode(2.0, datetime(2026, 8, 21, 10, tzinfo=UTC)), ("full", "T-2h"))

    def test_news_every_three_hours_from_t30_to_t6(self):
        self.assertEqual(decide_mode(20, datetime(2026, 8, 21, 9, tzinfo=UTC)), ("news", "news-3h"))
        self.assertEqual(decide_mode(20, datetime(2026, 8, 21, 10, tzinfo=UTC)), ("noop", "news-cadence"))

    def test_news_hourly_inside_six_hours(self):
        self.assertEqual(decide_mode(5, datetime(2026, 8, 21, 10, tzinfo=UTC)), ("news", "news-hourly"))

    def test_stops_at_forty_five_minutes(self):
        self.assertEqual(decide_mode(0.5, datetime(2026, 8, 21, 10, tzinfo=UTC)), ("noop", "deadline-lock"))

    def test_completed_t2_full_run_downgrades_later_tick_to_news(self):
        events = {"events": [{"id": 1, "is_next": True, "deadline_time": "2026-08-21T17:30:00Z"}]}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(events).encode()
        with tempfile.TemporaryDirectory() as tmp, patch.object(refresh, "MARKER", Path(tmp) / "last.json"), \
                patch.object(refresh.urllib.request, "urlopen", return_value=response), \
                patch.object(refresh, "datetime") as clock, patch.dict(os.environ, {}, clear=True):
            refresh.MARKER.write_text('{"gw":1,"done":["T-2h"]}')
            clock.now.return_value = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            with patch.object(refresh, "out") as output:
                refresh.main()
            output.assert_called_once_with(run='true', mode='news', reason='news-hourly', gw=1, hours='1.5')


class ApiUnreachableTests(unittest.TestCase):
    """The gate must retry before failing open, and fail open with gw=0 so the
    workflow skips mark()/notify (a GW0 mark would wipe the real GW's done-list)."""

    def _run(self, urlopen_mock):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(refresh, "MARKER", Path(tmp) / "last.json"), \
                patch.object(refresh.urllib.request, "urlopen", urlopen_mock), \
                patch.object(refresh.time, "sleep") as snooze, \
                patch.dict(os.environ, {}, clear=True):
            with patch.object(refresh, "out") as output:
                refresh.main()
        return output, snooze

    def test_fails_open_with_gw_zero_after_retries(self):
        import urllib.error
        calls = []

        def boom(req, timeout):
            calls.append(req)
            raise urllib.error.URLError("down")

        output, snooze = self._run(boom)
        self.assertEqual(len(calls), 3)          # initial attempt + 2 retries
        self.assertEqual(snooze.call_count, 2)
        output.assert_called_once_with(
            run='true', mode='full', reason='api-unreachable', gw='0', hours='0')

    def test_transient_failure_recovers_without_fail_open(self):
        events = {"events": [{"id": 2, "is_next": True,
                              "deadline_time": "2026-08-21T17:30:00Z"}]}
        ok = unittest.mock.MagicMock()
        ok.__enter__.return_value.read.return_value = json.dumps(events).encode()
        responses = [unittest.mock.MagicMock(side_effect=OSError("blip")), ok]

        def flaky(req, timeout):
            return responses.pop(0)

        output, snooze = self._run(flaky)
        snooze.assert_called_once()
        # One blip then success: the gate must sleep once and proceed down the
        # normal path (real GW id, whatever window decide_mode picks), not the
        # api-unreachable fail-open.
        kwargs = output.call_args.kwargs
        self.assertNotEqual(kwargs["reason"], "api-unreachable")
        self.assertEqual(kwargs["gw"], 2)


if __name__ == "__main__":
    unittest.main()
