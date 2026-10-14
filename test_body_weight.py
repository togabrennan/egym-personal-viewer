import argparse
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import fetch


def history(*items):
    return [{'type': 'WEIGHT_KG', 'history': list(items)}]


class BodyWeightFetchTests(unittest.TestCase):
    def test_normalize_dates_values_and_corrected_measurements(self):
        old = history({'date': '2026-07-02', 'value': 100}, {'date': '2026-07-01', 'value': 101})
        fresh = history({'date': '2026-07-02', 'value': 99},
                        {'date': 'invalid', 'value': 100}, {'date': '2026-07-03', 'value': 0},
                        {'date': '2026-07-04', 'value': float('nan')})
        merged = fetch.merge_body_weight(old, fresh)
        self.assertEqual(fetch.flatten_body_weight(merged), [
            {'date': '2026-07-01', 'weight_kg': 101}, {'date': '2026-07-02', 'weight_kg': 99}])
        self.assertEqual(fetch.flatten_body_weight([{'type': 'BMI', 'history': [{'date': '2026-07-01', 'value': 25}]}]), [])

    def test_request_uses_daily_local_dates_and_reports_failure(self):
        now = datetime(2026, 7, 21, tzinfo=timezone.utc)
        with patch.object(fetch, 'fetch', return_value=[]) as request:
            self.assertEqual(fetch.get_body_weight_history('user', 'cookie', now, now, 'America/New_York'), [])
            url, cookie = request.call_args.args
            self.assertIn('/body/history?', url)
            self.assertIn('types=WEIGHT_KG', url)
            self.assertIn('granularity=ONE_ITEM_PER_DAY', url)
            self.assertIn('timezone=America%2FNew_York', url)
            self.assertEqual(cookie, 'cookie')
            request.side_effect = RuntimeError('offline')
            self.assertIsNone(fetch.get_body_weight_history('user', 'cookie', now, now, 'UTC'))

    def test_failed_refresh_retains_weights_and_reflatten_preserves_them(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fetch, 'DATA_DIR', Path(tmp)):
            prior = history({'date': '2026-07-01', 'value': 100})
            raw = {'workouts': [{'code': 'w1', 'completedAt': datetime.now(timezone.utc).isoformat(),
                                  'timezone': 'America/New_York'}],
                   'body_weight': prior, 'body_weight_timezone': 'America/New_York'}
            args = argparse.Namespace(reflatten=False, full=False, years=10, timezone=None)
            with patch.object(fetch, 'parse_args', return_value=args), \
                 patch.object(fetch, 'resolve_credentials', return_value=('brand', 'user', 'secret')), \
                 patch.object(fetch, 'login', return_value=('uid', 'cookie')), \
                 patch.object(fetch, 'load_existing_raw', return_value=raw), \
                 patch.object(fetch, 'get_workouts', return_value={'workouts': []}), \
                 patch.object(fetch, 'get_strength_history', return_value={}), \
                 patch.object(fetch, 'get_bio_age', return_value={}), \
                 patch.object(fetch, 'get_body_weight_history', return_value=None), \
                 patch.object(fetch, 'HERE', Path(tmp).parent):
                fetch.main()
                saved = json.loads((Path(tmp) / 'workouts.json').read_text())
                self.assertEqual(saved['body_weight'], [{'date': '2026-07-01', 'weight_kg': 100}])
                self.assertEqual(saved['body_weight_timezone'], 'America/New_York')
                fetch.reflatten()
                self.assertEqual(json.loads((Path(tmp) / 'workouts.json').read_text())['body_weight'], saved['body_weight'])


if __name__ == '__main__':
    unittest.main()
