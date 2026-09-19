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


class TeamRecordsTests(unittest.TestCase):
    """Exercise update_team_records against a small synthetic race archive."""

    def _write(self, path, data):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)

    def _result(self, constructor_id, position, grid, driver='Ada Lovelace',
                fastest_rank=None):
        given, family = driver.split(' ', 1)
        result = {
            'position': position,
            'grid': grid,
            'Driver': {'givenName': given, 'familyName': family},
            'Constructor': {'constructorId': constructor_id},
        }
        if fastest_rank is not None:
            result['FastestLap'] = {'rank': fastest_rank}
        return result

    def _standings_file(self, constructor_id, driver='Ada Lovelace'):
        given, family = driver.split(' ', 1)
        return {'1': [{
            'position': '1',
            'Constructor': {'constructorId': constructor_id, 'name': constructor_id},
            'Driver': {'givenName': given, 'familyName': family},
            'Constructors': [{'constructorId': constructor_id}],
        }]}

    def test_lineage_totals_titles_and_pole_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            os.chdir(directory)
            try:
                # Pre-2003 season: poles come from grid position 1. Completed
                # (every scheduled race has results).
                self._write('races/2000/raceDetails.json', [{'round': '1'}])
                self._write('races/2000/results.json', [{
                    'round': '1', 'raceName': 'Old GP',
                    'Results': [self._result('oldid', '1', '1')],
                }])
                self._write('races/2000/constructorStandings.json',
                            self._standings_file('oldid'))
                self._write('races/2000/driverStandings.json',
                            self._standings_file('oldid'))

                # 2010+ season: poles come from qualifying data. Completed.
                self._write('races/2015/raceDetails.json', [{'round': '1'}])
                self._write('races/2015/results.json', [{
                    'round': '1', 'raceName': 'New GP',
                    'Results': [self._result('newid', '1', '5',
                                             fastest_rank='1')],
                }])
                self._write('races/2015/qualifying.json', [{
                    'round': '1',
                    'QualifyingResults': [{
                        'position': '1',
                        'Driver': {'givenName': 'Ada', 'familyName': 'Lovelace'},
                        'Constructor': {'constructorId': 'newid'},
                    }],
                }])
                self._write('races/2015/constructorStandings.json', {'1': []})
                self._write('races/2015/driverStandings.json', {'1': []})

                # A PAST but partially fetched season (scheduled 2, only 1 stored):
                # its standings leader must NOT count as champion. This guards the
                # season-completeness gate against trusting the calendar year.
                self._write('races/2020/raceDetails.json',
                            [{'round': '1'}, {'round': '2'}])
                self._write('races/2020/results.json', [{
                    'round': '1', 'raceName': 'Live GP',
                    'Results': [self._result('newid', '1', '1',
                                             fastest_rank='1')],
                }])
                self._write('races/2020/qualifying.json', [{
                    'round': '1',
                    'QualifyingResults': [{
                        'position': '1',
                        'Driver': {'givenName': 'Ada', 'familyName': 'Lovelace'},
                        'Constructor': {'constructorId': 'newid'},
                    }],
                }])
                self._write('races/2020/constructorStandings.json',
                            self._standings_file('newid'))
                self._write('races/2020/driverStandings.json',
                            self._standings_file('newid'))

                self._write('teamLineage.json', {'teams': {'combo': {
                    'name': 'Combo', 'color': '#000000', 'lineage': [
                        {'team': 'Old', 'constructorIds': ['oldid'],
                         'startYear': 2000, 'endYear': 2009},
                        {'team': 'New', 'constructorIds': ['newid'],
                         'startYear': 2010, 'endYear': None},
                    ],
                }}})

                with mock.patch.object(api_update, 'current_year', 2021):
                    api_update.update_team_records()

                with open('teamRecords.json', encoding='utf-8') as handle:
                    records = json.load(handle)

                team = records['teams']['combo']
                total = team['total']
                self.assertEqual(total['wins'], 3)  # 2000 + 2015 + 2020
                self.assertEqual(total['poles'], 3)  # grid, qualifying, qualifying
                self.assertEqual(total['fastestLaps'], 2)  # only 2015 + 2020
                # Only the completed 2000 season yields a title; 2020 is excluded.
                self.assertEqual([t['year'] for t in total['constructorTitles']],
                                 [2000])
                self.assertEqual([t['year'] for t in total['driverTitles']], [2000])
                self.assertTrue(team['lineage'][1]['current'])
                self.assertFalse(team['lineage'][0]['current'])
            finally:
                os.chdir(previous_directory)


if __name__ == '__main__':
    unittest.main()
