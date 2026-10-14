import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import mcp_server as mcp
import serve


class MCPToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_path = Path(self.tmp.name) / 'workouts.json'
        self.patch = patch.object(serve, 'DATA', self.data_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.clock = patch.object(mcp, '_now', return_value=datetime(2026, 7, 21, 12, tzinfo=timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.fixture = {
            'generated_at': '2026-07-21T10:00:00Z', 'workout_count': 2,
            'rows': [self.row(1), self.row(2)],
            'strength': [self.strength('Leg Press', 100, 1), self.strength('Leg Press', 150, 2),
                         self.strength('Leg Press', 120, 3), self.strength('Chest Press', 50, 1)],
            'bio_age': {
                'totalDetails': {'totalBioAge': {'value': 30, 'progress': 'down', 'createdAt': '2026-07-21T01:30:00Z', 'timezone': 'America/New_York'}},
                'cardioDetails': {'restingHeartRate': {'value': 55}, 'vo2max': {'value': 42}, 'cardioAge': None},
                'metabolicDetails': {'bmi': {'value': 24}, 'bodyFat': {'value': 20}},
                'flexibilityDetails': None,
            },
        }

    def row(self, index):
        return {'workout_completed_at': '2026-07-21T01:30:00Z',
                'exercise_completed_at': '2026-07-21T01:30:00Z',
                'workout_timezone': 'America/New_York', 'exercise_name': 'Leg Press',
                'workout_code': 'w1', 'exercise_code': 'ex1', 'weight': 10,
                'reps': 5, 'set_index': index, 'activity_points': 7}

    def strength(self, name, kg, day):
        return {'exercise_name': name, 'one_rep_max_kg': kg, 'measured_at': f'2026-07-{day:02d}T12:00:00Z'}

    def write(self):
        self.data_path.write_text(json.dumps(self.fixture))

    def test_all_read_tools_missing_file(self):
        for tool, args in [(mcp.get_summary, ()), (mcp.get_gym_days, ()), (mcp.get_day, ('2026-07-20',)),
                           (mcp.get_strength, ()), (mcp.get_machine_history, ('leg',)), (mcp.get_bio_age, ())]:
            with self.subTest(tool=tool.__name__):
                self.assertEqual(tool(*args), mcp.NO_DATA)

    def test_timezone_weights_volume_and_points(self):
        self.write()
        days = mcp.get_gym_days()
        self.assertIn('2026-07-20', days)
        self.assertNotIn('2026-07-21', days)
        self.assertIn('220.5 lb', days)
        self.assertIn('| 2 | 220.5 lb | 7 |', days)
        self.assertIn('22.0 lb', mcp.get_day('2026-07-20'))
        self.assertIn('10.0 kg', mcp.get_day('2026-07-20', 'kg'))
        self.assertIn('No workout', mcp.get_day('2026-07-21'))

    def test_summary_and_reload(self):
        self.write()
        summary = mcp.get_summary()
        self.assertIn('synced 2.0 h ago', summary)
        self.assertIn('| Gym days in last 7 days | 1 |', summary)
        self.fixture['workout_count'] = 99
        self.write()
        self.assertIn('| Total workouts | 99 |', mcp.get_summary())

    def test_strength_latest_best_change_single_test_and_sort(self):
        self.write()
        result = mcp.get_strength(unit='kg')
        self.assertIn('| Leg Press | 120.0 kg | 2026-07-03 | 150.0 kg | 2026-07-02 | -30.0 kg (-20.0%) | 3 |', result)
        self.assertIn('| Chest Press | 50.0 kg | 2026-07-01 | 50.0 kg | 2026-07-01 | — | 1 |', result)
        self.assertLess(result.index('Leg Press'), result.index('Chest Press'))
        self.assertIn('264.6 lb', mcp.get_strength())
        self.fixture['strength'].append(self.strength('Zero', 0, 1))
        self.fixture['strength'].append(self.strength('Zero', 10, 2))
        self.write()
        self.assertIn('+10.0 kg (—%)', mcp.get_strength(unit='kg'))

    def test_matching_and_chronological_history(self):
        self.write()
        for name in ['Leg Press', 'LEG PRESS', 'lEg']:
            result = mcp.get_strength(name)
            self.assertIn('1RM history', result)
            self.assertLess(result.index('2026-07-01'), result.index('2026-07-03'))
        ambiguous = mcp.get_strength('press')
        self.assertIn('Ambiguous', ambiguous)
        self.assertIn('Chest Press', ambiguous)
        self.assertIn('Leg Press', ambiguous)
        self.assertIn('Available machines', mcp.get_strength('missing'))
        self.assertIn('Available machines', mcp.get_strength(''))

    def test_machine_history_and_window(self):
        self.write()
        result = mcp.get_machine_history('LEG', unit='kg')
        self.assertIn('2 × 5 @ 10.0 kg', result)
        self.assertIn('| 10.0 kg | 100.0 kg |', result)
        self.assertNotIn('2026-07-20', mcp.get_machine_history('leg', days=1))
        self.fixture['rows'].append({**self.row(1), 'exercise_name': 'Leg Curl'})
        self.write()
        self.assertIn('Ambiguous', mcp.get_machine_history('leg'))

    def test_bio_age_skips_nulls(self):
        self.write()
        result = mcp.get_bio_age()
        self.assertIn('| Total bio age | 30 years | down | 2026-07-20 |', result)
        for name in ['RHR', 'VO₂max', 'BMI', 'Body fat']:
            self.assertIn(name, result)
        self.assertNotIn('Flexibility', result)
        self.assertNotIn('None', result)

    def test_bad_inputs_and_empty_data(self):
        self.write()
        self.assertIn('YYYY-MM-DD', mcp.get_day('bad'))
        self.assertIn('valid date', mcp.get_day('2026-02-30'))
        self.assertIn('positive integer', mcp.get_gym_days(0))
        self.assertIn('unit must', mcp.get_gym_days(unit='oz'))
        self.fixture = {}
        self.write()
        self.assertIn('none', mcp.get_summary())
        self.assertIn('No bio-age', mcp.get_bio_age())
        self.assertIn('No matching', mcp.get_strength('leg'))

    def test_refresh_busy_failure_success_and_lock_release(self):
        with patch.object(serve, 'run_fetch') as fetch:
            serve.FETCH_LOCK.acquire()
            try:
                self.assertEqual(mcp.refresh(), 'A fetch is already running')
                fetch.assert_not_called()
            finally:
                serve.FETCH_LOCK.release()
            fetch.return_value = 'Fetch failed (exit code 1)'
            self.assertIn('Fetch failed', mcp.refresh())
            fetch.side_effect = OSError('offline')
            self.assertIn('offline', mcp.refresh())
            self.assertFalse(serve.FETCH_LOCK.locked())
            fetch.side_effect = None
            fetch.return_value = 'A fetch is already running'
            self.assertEqual(mcp.refresh(), 'A fetch is already running')
            fetch.return_value = None
            self.write()
            self.assertEqual(mcp.refresh(), 'Refreshed — synced at 2026-07-21T10:00:00Z')

    def test_streamable_http_tools_and_security(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest('Run with uv --with mcp==2.3.0 for HTTP integration checks')
        self.write()
        path = '/' + 'a' * 43 + '/mcp'
        headers = {'accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}
        with patch.dict(os.environ, {'EGYM_MCP_ALLOWED_HOSTS': 'egym-server.example.ts.net'}):
            app = mcp.create_app(path)
        with TestClient(app, base_url='http://127.0.0.1:8000') as client:
            def rpc(method, params):
                response = client.post(path, headers=headers, json={
                    'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()['result']

            self.assertEqual(client.post('/wrong/mcp', headers=headers).status_code, 404)
            self.assertEqual(client.post('/mcp', headers=headers).status_code, 404)
            self.assertEqual(client.post(path + '/', headers=headers).status_code, 404)
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}
            self.assertEqual(client.post(path, headers={**headers, 'host': 'evil.example'}, json=request).status_code, 421)
            self.assertEqual(client.post(path, headers={**headers, 'origin': 'https://evil.example'}, json=request).status_code, 403)
            init = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                      'clientInfo': {'name': 'egym-test', 'version': '1'}})
            self.assertEqual(init['serverInfo']['name'], 'eGym')
            tools = rpc('tools/list', {})['tools']
            self.assertEqual(len(tools), 7)
            for tool in tools:
                self.assertEqual(tool['annotations']['readOnlyHint'], tool['name'] != 'refresh')
            with patch.object(serve, 'run_fetch', return_value=None):
                for name, args in [('get_summary', {}), ('get_gym_days', {}),
                                   ('get_day', {'date': '2026-07-20'}), ('get_strength', {'machine': 'leg'}),
                                   ('get_machine_history', {'machine': 'leg'}), ('get_bio_age', {}), ('refresh', {})]:
                    with self.subTest(tool=name):
                        result = rpc('tools/call', {'name': name, 'arguments': args})
                        self.assertFalse(result.get('isError'), result)
                        self.assertEqual(result['content'][0]['type'], 'text')
                        self.assertNotIn('structuredContent', result)
            for extra in [{'host': 'egym-server.example.ts.net'}, {'origin': 'https://egym-server.example.ts.net'}]:
                response = client.post(path, headers={**headers, **extra}, json={
                    'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
                self.assertEqual(response.status_code, 200, response.text)


class ConfigurationTests(unittest.TestCase):
    def test_token_and_loopback_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                mcp.endpoint_path('127.0.0.1', False)
            self.assertEqual(mcp.endpoint_path('127.0.0.1', True), '/mcp')
            for host in ['0.0.0.0', '192.168.1.2']:
                with self.assertRaises(ValueError):
                    mcp.endpoint_path(host, True)
            os.environ['EGYM_MCP_TOKEN'] = 'secret/path'
            with self.assertRaises(ValueError):
                mcp.endpoint_path('127.0.0.1', False)
            os.environ['EGYM_MCP_TOKEN'] = 'a' * 43
            self.assertEqual(mcp.endpoint_path('127.0.0.1', False), '/' + 'a' * 43 + '/mcp')


if __name__ == '__main__':
    unittest.main()
