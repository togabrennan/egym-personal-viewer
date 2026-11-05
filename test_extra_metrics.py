import argparse
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import fetch
import mcp_server
import serve


class ExtraMetricsTests(unittest.TestCase):
    def setUp(self):
        self.snapshots = {
            'muscle_imbalances': {'muscleImbalances': [
                {'agonistMuscle': 'CHEST', 'position': 0.6, 'optimalRangeStartPosition': 0.4}]},
            'flexibility': [{'type': 'HIP_DEG', 'value': 80, 'valueInterpretation': 'GOOD'}],
            'activity_level': {'points': 0, 'daysLeft': 10, 'level': 'BRONZE', 'goal': 100},
            'body_measurements': [{'type': 'BODY_FAT_PERCENT', 'value': 20, 'source': 'scale'},
                                  {'type': 'NEW_METRIC', 'value': 42}],
        }

    def test_requests_preserve_all_fields_and_measurement_types(self):
        with patch.object(fetch, 'fetch', side_effect=list(self.snapshots.values())) as request:
            self.assertEqual(fetch.get_extra_metrics('uid', 'cookie', {}), self.snapshots)
        self.assertEqual([call.args for call in request.call_args_list], [
            (f'{fetch.EGYM_API}/analysis/api/v1.0/exercisers/uid/muscleimbalances', 'cookie'),
            (f'{fetch.EGYM_API}/measurements/api/v1.0/exercisers/uid/flexibility/latest', 'cookie'),
            (f'{fetch.EGYM_API}/analysis/api/v1.0/exercisers/uid/activitylevels', 'cookie'),
            (f'{fetch.EGYM_API}/measurements/api/v1.0/exercisers/uid/body/latest', 'cookie'),
        ])

    def test_failure_and_malformed_payloads_retain_cache_but_empty_success_clears(self):
        with patch.object(fetch, 'fetch', side_effect=[RuntimeError('403'), [], {}, []]):
            result = fetch.get_extra_metrics('uid', 'cookie', self.snapshots)
        self.assertEqual(result['muscle_imbalances'], self.snapshots['muscle_imbalances'])
        self.assertEqual(result['flexibility'], [])
        self.assertEqual(result['activity_level'], {})
        self.assertEqual(result['body_measurements'], [])
        with patch.object(fetch, 'fetch', side_effect=[{}, {}, [], {'error': 'unauthorized'}]):
            self.assertEqual(fetch.get_extra_metrics('uid', 'cookie', self.snapshots), self.snapshots)

    def test_sync_reflatten_and_mcp_roundtrip_with_partial_failure(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            directory = Path(tmp)
            stack.enter_context(patch.object(fetch, 'DATA_DIR', directory))
            stack.enter_context(patch.object(fetch, 'HERE', directory.parent))
            stack.enter_context(patch.object(serve, 'DATA', directory / 'workouts.json'))
            args = argparse.Namespace(reflatten=False, full=False, years=1, timezone='UTC')
            mocks = {
                'parse_args': args, 'resolve_credentials': ('brand', 'user', 'secret'),
                'login': ('uid', 'cookie'),
                'load_existing_raw': {'workouts': [{'code': 'w1'}], **self.snapshots},
                'get_workouts': {'workouts': []}, 'get_strength_history': {},
                'get_bio_age': {}, 'get_body_weight_history': [],
            }
            for name, result in mocks.items():
                stack.enter_context(patch.object(fetch, name, return_value=result))
            with patch.object(fetch, 'fetch', side_effect=[
                    self.snapshots['muscle_imbalances'], RuntimeError('offline'),
                    self.snapshots['activity_level'], self.snapshots['body_measurements']]):
                fetch.main()
            for filename in ('workouts_raw.json', 'workouts.json'):
                saved = json.loads((directory / filename).read_text())
                self.assertEqual({key: saved[key] for key in self.snapshots}, self.snapshots)
            # Use the real cache loader when rebuilding outputs.
            cached = json.loads((directory / 'workouts_raw.json').read_text())
            with patch.object(fetch, 'load_existing_raw', return_value=cached):
                fetch.reflatten()
            self.assertEqual(json.loads(mcp_server.get_extra_metrics()), self.snapshots)
            self.assertEqual(json.loads(mcp_server.get_extra_metrics('body_measurements')),
                             {'body_measurements': self.snapshots['body_measurements']})
            self.assertIn('Unknown metric', mcp_server.get_extra_metrics('unknown'))

    def test_old_cache_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'workouts.json'
            with patch.object(serve, 'DATA', path):
                self.assertEqual(mcp_server.get_extra_metrics(), mcp_server.NO_DATA)
                path.write_text('{}')
                self.assertEqual(json.loads(mcp_server.get_extra_metrics()),
                                 {key: None for key in self.snapshots})


if __name__ == '__main__':
    unittest.main()
