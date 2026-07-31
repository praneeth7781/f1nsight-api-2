import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import api_update


class AdditiveRoundUpdateTests(unittest.TestCase):
    def test_merge_preserves_existing_round(self):
        existing = [
            {
                'round': '1',
                'raceName': 'Existing race',
                'Results': ['stored'],
            }
        ]
        additions = [
            {
                'round': '1',
                'raceName': 'Replacement race',
                'Results': ['replacement'],
            },
            {
                'round': '2',
                'raceName': 'New race',
                'Results': ['new'],
            },
        ]

        merged = api_update.merge_round_records(existing, additions)

        self.assertEqual([record['round'] for record in merged], ['1', '2'])
        self.assertEqual(merged[0]['raceName'], 'Existing race')
        self.assertEqual(merged[0]['Results'], ['stored'])

    def test_failed_missing_round_fetch_does_not_modify_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = os.getcwd()
            os.chdir(temporary_directory)
            try:
                os.makedirs('races/2026')
                calendar = [
                    {
                        'round': '1',
                        'raceName': 'Stored Grand Prix',
                        'date': '2000-01-01',
                    },
                    {
                        'round': '2',
                        'raceName': 'Missing Grand Prix',
                        'date': '2000-01-08',
                    },
                ]
                existing = [
                    {
                        'round': '1',
                        'raceName': 'Stored Grand Prix',
                        'Results': ['stored'],
                    }
                ]
                with open(
                    'races/2026/raceDetails.json',
                    'w',
                    encoding='utf-8',
                ) as file:
                    json.dump(calendar, file)
                with open(
                    'races/2026/results.json',
                    'w',
                    encoding='utf-8',
                ) as file:
                    json.dump(existing, file)

                with open('races/2026/results.json', 'rb') as file:
                    original_bytes = file.read()

                with (
                    mock.patch.object(api_update, 'current_year', 2026),
                    mock.patch.object(
                        api_update,
                        'api_races',
                        side_effect=RuntimeError('simulated API failure'),
                    ),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        'simulated API failure',
                    ):
                        api_update.update_raceResults()

                with open('races/2026/results.json', 'rb') as file:
                    self.assertEqual(file.read(), original_bytes)
            finally:
                os.chdir(previous_directory)

    def test_missing_round_is_appended_without_replacing_stored_round(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = os.getcwd()
            os.chdir(temporary_directory)
            try:
                os.makedirs('races/2026')
                calendar = [
                    {
                        'round': '1',
                        'raceName': 'Stored Grand Prix',
                        'date': '2000-01-01',
                    },
                    {
                        'round': '2',
                        'raceName': 'New Grand Prix',
                        'date': '2000-01-08',
                    },
                ]
                existing = [
                    {
                        'round': '1',
                        'raceName': 'Stored Grand Prix',
                        'Results': ['stored'],
                    }
                ]
                with open(
                    'races/2026/raceDetails.json',
                    'w',
                    encoding='utf-8',
                ) as file:
                    json.dump(calendar, file)
                with open(
                    'races/2026/results.json',
                    'w',
                    encoding='utf-8',
                ) as file:
                    json.dump(existing, file)

                fetched = {
                    'round': '2',
                    'raceName': 'New Grand Prix',
                    'Results': ['new'],
                }
                with (
                    mock.patch.object(api_update, 'current_year', 2026),
                    mock.patch.object(
                        api_update,
                        'api_races',
                        return_value=[fetched],
                    ) as api_races_mock,
                ):
                    api_update.update_raceResults()

                with open(
                    'races/2026/results.json',
                    'r',
                    encoding='utf-8',
                ) as file:
                    updated = json.load(file)

                self.assertEqual(
                    [record['round'] for record in updated],
                    ['1', '2'],
                )
                self.assertEqual(updated[0]['Results'], ['stored'])
                self.assertEqual(updated[1]['Results'], ['new'])
                api_races_mock.assert_called_once_with(
                    f'{api_update.api_url}/2026/2/results.json'
                )
            finally:
                os.chdir(previous_directory)


class ApiRetryTests(unittest.TestCase):
    def test_rate_limit_is_retried(self):
        throttled = SimpleNamespace(
            status_code=429,
            headers={'Retry-After': '2'},
        )
        success = SimpleNamespace(status_code=200, headers={})

        with (
            mock.patch.object(
                api_update.api_session,
                'get',
                side_effect=[throttled, success],
            ) as get_mock,
            mock.patch.object(api_update.time, 'sleep') as sleep_mock,
            mock.patch.object(
                api_update.time,
                'monotonic',
                side_effect=[10.0, 10.0, 11.0, 11.0],
            ),
            mock.patch.object(api_update, 'last_api_request_at', 0.0),
        ):
            response = api_update.api_get('https://example.test/data.json')

        self.assertIs(response, success)
        self.assertEqual(get_mock.call_count, 2)
        sleep_mock.assert_any_call(2.0)


if __name__ == '__main__':
    unittest.main()
