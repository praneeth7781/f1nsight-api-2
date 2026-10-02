import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

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


class PaginationTests(unittest.TestCase):
    def _json_response(self, payload):
        return SimpleNamespace(json=lambda payload=payload: payload)

    def test_results_fetch_requests_limit_above_30(self):
        payload = {
            'MRData': {
                'limit': '100',
                'offset': '0',
                'total': '22',
                'RaceTable': {
                    'Races': [{
                        'season': '2026',
                        'round': '2',
                        'Results': [{'position': str(i)} for i in range(1, 23)],
                    }]
                },
            }
        }
        with mock.patch.object(
            api_update,
            'api_get',
            return_value=self._json_response(payload),
        ) as get_mock:
            races = api_update.api_races(
                f'{api_update.api_url}/2026/2/results.json'
            )

        url = get_mock.call_args[0][0]
        self.assertIn('limit=100', url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertGreaterEqual(int(query['limit'][0]), 100)
        self.assertEqual(len(races[0]['Results']), 22)

    def test_paginates_until_mrdata_total(self):
        first = {
            'MRData': {
                'limit': '100',
                'offset': '0',
                'total': '120',
                'RaceTable': {
                    'Races': [{
                        'season': '1952',
                        'round': '2',
                        'Results': [{'position': str(i)} for i in range(1, 101)],
                    }]
                },
            }
        }
        second = {
            'MRData': {
                'limit': '100',
                'offset': '100',
                'total': '120',
                'RaceTable': {
                    'Races': [{
                        'season': '1952',
                        'round': '2',
                        'Results': [{'position': str(i)} for i in range(101, 121)],
                    }]
                },
            }
        }
        with mock.patch.object(
            api_update,
            'api_get',
            side_effect=[
                self._json_response(first),
                self._json_response(second),
            ],
        ) as get_mock:
            races = api_update.api_races(
                f'{api_update.api_url}/1952/2/results.json'
            )

        self.assertEqual(get_mock.call_count, 2)
        self.assertIn('offset=100', get_mock.call_args_list[1][0][0])
        self.assertEqual(len(races[0]['Results']), 120)
        self.assertEqual(races[0]['Results'][0]['position'], '1')
        self.assertEqual(races[0]['Results'][-1]['position'], '120')

    def test_standings_fetch_requests_limit_above_30(self):
        payload = {
            'MRData': {
                'limit': '100',
                'offset': '0',
                'total': '80',
                'StandingsTable': {
                    'StandingsLists': [{
                        'season': '1952',
                        'round': '8',
                        'DriverStandings': [
                            {'position': str(i)} for i in range(1, 81)
                        ],
                    }]
                },
            }
        }
        with mock.patch.object(
            api_update,
            'api_get',
            return_value=self._json_response(payload),
        ) as get_mock:
            data = api_update.api_get_json(
                f'{api_update.api_url}/1952/8/driverStandings.json'
            )

        url = get_mock.call_args[0][0]
        self.assertIn('limit=100', url)
        standings = data['MRData']['StandingsTable']['StandingsLists'][0]
        self.assertEqual(len(standings['DriverStandings']), 80)


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
            'Driver': {
                'driverId': family.lower(),
                'givenName': given,
                'familyName': family,
            },
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
            'Driver': {
                'driverId': family.lower(),
                'givenName': given,
                'familyName': family,
            },
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
                        'Driver': {
                            'driverId': 'lovelace',
                            'givenName': 'Ada',
                            'familyName': 'Lovelace',
                        },
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
                        'Driver': {
                            'driverId': 'lovelace',
                            'givenName': 'Ada',
                            'familyName': 'Lovelace',
                        },
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
                self.assertIn('leaders', total)
                self.assertGreaterEqual(len(total['leaders']['starts']), 1)
            finally:
                os.chdir(previous_directory)


class ScoringTests(unittest.TestCase):
    def test_fractional_points_are_not_truncated(self):
        self.assertEqual(api_update.parse_points('28.5'), 28.5)
        self.assertEqual(api_update.parse_points('4.14'), 4.14)
        self.assertEqual(api_update.parse_points(1 / 7), 1 / 7)

    def test_1954_british_gp_overrides(self):
        import scoring
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            os.chdir(directory)
            try:
                seventh = 1 / 7
                os.makedirs('races/1954')
                results = [{
                    'season': '1954',
                    'round': '5',
                    'raceName': 'British Grand Prix',
                    'Results': [
                        {'number': '9', 'position': '1', 'positionText': '1',
                         'points': '8.14', 'Driver': {'driverId': 'gonzalez'}},
                        {'number': '7', 'position': '16', 'positionText': 'R',
                         'points': '0', 'Driver': {'driverId': 'moss'}},
                        {'number': '17', 'position': '18', 'positionText': 'R',
                         'points': '0', 'Driver': {'driverId': 'behra'}},
                        {'number': '32', 'position': '21', 'positionText': 'R',
                         'points': '0', 'Driver': {'driverId': 'ascari'}},
                        {'number': '31', 'position': '24', 'positionText': 'R',
                         'points': '0', 'Driver': {'driverId': 'ascari'}},
                    ],
                }]
                standings = {'9': [
                    {'position': '15', 'points': '4',
                     'Driver': {'driverId': 'moss'}},
                    {'position': '25', 'points': '1',
                     'Driver': {'driverId': 'ascari'}},
                    {'position': '28', 'points': '0',
                     'Driver': {'driverId': 'behra'}},
                ], '5': [
                    {'position': '15', 'points': '4',
                     'Driver': {'driverId': 'moss'}},
                ]}
                with open('races/1954/results.json', 'w', encoding='utf-8') as handle:
                    json.dump(results, handle)
                with open(
                    'races/1954/driverStandings.json', 'w', encoding='utf-8'
                ) as handle:
                    json.dump(standings, handle)
                with open('scoringOverrides.json', 'w', encoding='utf-8') as handle:
                    json.dump({
                        'overrides': [
                            {'target': 'results', 'season': '1954', 'round': '5',
                             'driverId': 'moss', 'field': 'points', 'value': seventh},
                            {'target': 'results', 'season': '1954', 'round': '5',
                             'driverId': 'behra', 'field': 'points', 'value': seventh},
                            {'target': 'results', 'season': '1954', 'round': '5',
                             'driverId': 'ascari', 'number': '32', 'field': 'points',
                             'value': seventh},
                            {'target': 'results', 'season': '1954', 'round': '5',
                             'driverId': 'ascari', 'number': '31', 'field': 'points',
                             'value': 0},
                            {'target': 'driverStandings', 'season': '1954',
                             'roundFrom': 5, 'driverId': 'moss', 'field': 'points',
                             'add': seventh},
                            {'target': 'driverStandings', 'season': '1954',
                             'roundFrom': 5, 'driverId': 'ascari', 'field': 'points',
                             'add': seventh},
                            {'target': 'driverStandings', 'season': '1954',
                             'roundFrom': 5, 'driverId': 'behra', 'field': 'points',
                             'add': seventh},
                        ]
                    }, handle)
                scoring.apply_scoring_overrides([1954])
                updated = json.load(open('races/1954/results.json'))[0]['Results']
                by_id = {}
                for row in updated:
                    by_id.setdefault(row['Driver']['driverId'], []).append(row)
                self.assertAlmostEqual(float(by_id['moss'][0]['points']), seventh)
                self.assertAlmostEqual(float(by_id['behra'][0]['points']), seventh)
                self.assertAlmostEqual(float(by_id['ascari'][0]['points']), seventh)
                self.assertEqual(float(by_id['ascari'][1]['points']), 0)
                final = json.load(open('races/1954/driverStandings.json'))['9']
                points = {
                    row['Driver']['driverId']: float(row['points']) for row in final
                }
                self.assertAlmostEqual(points['moss'], 4 + seventh)
                self.assertAlmostEqual(points['ascari'], 1 + seventh)
                self.assertAlmostEqual(points['behra'], seventh)
            finally:
                os.chdir(previous_directory)

    def test_dropped_scores_1964_and_1988(self):
        import scoring
        # 1964: Hill 41 gross / 39 counted vs Surtees 40.
        hill = [9, 9, 9, 6, 4, 2, 2, 0, 0, 0]
        surtees = [9, 9, 6, 6, 6, 4, 0, 0, 0, 0]
        rule = {'type': 'bestN', 'n': 6}
        hill_gross, hill_counted = scoring.apply_drop_rules(hill, rule)
        surtees_gross, surtees_counted = scoring.apply_drop_rules(surtees, rule)
        self.assertEqual(hill_gross, 41)
        self.assertEqual(hill_counted, 39)
        self.assertEqual(surtees_counted, 40)
        self.assertGreater(hill_counted, 0)
        self.assertLess(hill_counted, surtees_counted)
        _, hill_all = scoring.apply_drop_rules(hill, {'type': 'none'})
        _, surtees_all = scoring.apply_drop_rules(surtees, {'type': 'none'})
        self.assertGreater(hill_all, surtees_all)

        # 1988: Prost 105 / 87 vs Senna 94 / 90.
        prost = [9, 9, 9, 9, 9, 9, 9, 6, 6, 6, 6, 6, 6, 4, 2, 0]
        senna = [9, 9, 9, 9, 9, 9, 9, 9, 6, 6, 6, 4, 0, 0, 0, 0]
        self.assertEqual(sum(prost), 105)
        self.assertEqual(sum(senna), 94)
        rule = {'type': 'bestN', 'n': 11}
        prost_gross, prost_counted = scoring.apply_drop_rules(prost, rule)
        senna_gross, senna_counted = scoring.apply_drop_rules(senna, rule)
        self.assertEqual(prost_gross, 105)
        self.assertEqual(prost_counted, 87)
        self.assertEqual(senna_gross, 94)
        self.assertEqual(senna_counted, 90)
        self.assertLess(prost_counted, senna_counted)
        _, prost_all = scoring.apply_drop_rules(prost, {'type': 'none'})
        _, senna_all = scoring.apply_drop_rules(senna, {'type': 'none'})
        self.assertGreater(prost_all, senna_all)

    def test_shared_car_1956_and_multicar_drop(self):
        import scoring
        data = scoring.load_systems_file()
        era = scoring.system_for_year(1956, data)
        race = {
            'round': '1',
            'raceName': 'Argentine Grand Prix',
            'Results': [
                {'position': '1', 'positionText': '1', 'points': '5',
                 'Driver': {'driverId': 'fangio', 'givenName': 'Juan',
                            'familyName': 'Fangio'},
                 'Constructor': {'constructorId': 'ferrari'}},
                {'position': '1', 'positionText': '1', 'points': '4',
                 'Driver': {'driverId': 'musso', 'givenName': 'Luigi',
                            'familyName': 'Musso'},
                 'Constructor': {'constructorId': 'ferrari'}},
            ],
        }
        scored, _ = scoring.score_entries(1956, race, era, era, data)
        by_id = {row['driverId']: row for row in scored}
        self.assertTrue(by_id['fangio']['shared'])
        self.assertAlmostEqual(by_id['fangio']['points'], 5)
        self.assertAlmostEqual(by_id['musso']['points'], 4)
        shares = scoring.recover_fastest_lap_shares(1956, race, era, data)
        self.assertAlmostEqual(shares['fangio'], 1.0)
        # Two car results in one race both count toward dropping.
        gross, counted = scoring.apply_drop_rules(
            [5, 0, 8, 8, 6, 4, 0, 0],
            {'type': 'bestN', 'n': 5},
        )
        self.assertEqual(counted, 31)
        self.assertEqual(gross, 31)

    def test_split_drop_aligns_to_calendar_rounds(self):
        import scoring
        points = [9, 6, 4, 3, 2, 9]
        rounds = [1, 3, 4, 5, 6, 7]
        rule = {
            'type': 'split',
            'halves': [
                {'races': 6, 'best': 5},
                {'races': 5, 'best': 4},
            ],
        }
        _, counted = scoring.apply_drop_rules(points, rule, rounds=rounds)
        # First half rounds 1-6: [9,6,4,3,2] best 5 = 24.
        # Second half starts at round 7: [9].
        self.assertEqual(counted, 33)
        _, by_index = scoring.apply_drop_rules(points, rule)
        # List-index split treats the round-7 9 as a first-half score.
        self.assertEqual(by_index, 31)

    def test_fastest_lap_awarded_once_per_driver(self):
        import scoring
        data = scoring.load_systems_file()
        era = scoring.system_for_year(1954, data)
        race = {
            'round': '5',
            'raceName': 'British Grand Prix',
            'Results': [
                {'position': '21', 'positionText': 'R', 'points': 1 / 7,
                 'number': '32',
                 'Driver': {'driverId': 'ascari'},
                 'Constructor': {'constructorId': 'maserati'}},
                {'position': '24', 'positionText': 'R', 'points': 0,
                 'number': '31',
                 'Driver': {'driverId': 'ascari'},
                 'Constructor': {'constructorId': 'maserati'}},
            ],
        }
        scored, _ = scoring.score_entries(1954, race, era, era, data)
        # Isolated leftover 1/7 normalizes to a full share; still only once.
        self.assertAlmostEqual(sum(row['points'] for row in scored), 1.0)
        self.assertEqual(sum(1 for row in scored if row['flPoints']), 1)
        awarded, _ = scoring.score_entries(
            1954, race, era, era, data, awarded=True,
        )
        self.assertAlmostEqual(sum(row['flPoints'] for row in awarded), 1 / 7)

    def test_leftover_fl_ignores_shared_car_allocation_mismatch(self):
        import scoring
        data = scoring.load_systems_file()
        era = scoring.system_for_year(1957, data)
        race = {
            'round': '5',
            'raceName': 'British Grand Prix',
            'Results': [
                {'position': '1', 'positionText': '1', 'points': '5',
                 'Driver': {'driverId': 'moss'},
                 'Constructor': {'constructorId': 'vanwall'}},
                {'position': '1', 'positionText': '1', 'points': '4',
                 'Driver': {'driverId': 'brooks'},
                 'Constructor': {'constructorId': 'vanwall'}},
                {'position': '4', 'positionText': '4', 'points': '3',
                 'Driver': {'driverId': 'trintignant'},
                 'Constructor': {'constructorId': 'ferrari'}},
                {'position': '4', 'positionText': '4', 'points': '0',
                 'Driver': {'driverId': 'collins'},
                 'Constructor': {'constructorId': 'ferrari'}},
            ],
        }
        shares = scoring.recover_fastest_lap_shares(1957, race, era, data)
        self.assertAlmostEqual(shares['moss'], 1.0)
        self.assertNotIn('trintignant', shares)

    def test_2024_monaco_fl_recovered_without_rank(self):
        import scoring
        data = scoring.load_systems_file()
        era = scoring.system_for_year(2024, data)
        race = {
            'round': '8',
            'raceName': 'Monaco Grand Prix',
            'Results': [
                {'position': '7', 'positionText': '7', 'points': '7',
                 'Driver': {'driverId': 'hamilton'},
                 'Constructor': {'constructorId': 'mercedes'}},
                {'position': '1', 'positionText': '1', 'points': '25',
                 'Driver': {'driverId': 'leclerc'},
                 'Constructor': {'constructorId': 'ferrari'}},
            ],
        }
        recipients = scoring.fastest_lap_recipients(2024, race, era, data)
        self.assertAlmostEqual(recipients['hamilton'], 1.0)
        scored, _ = scoring.score_entries(2024, race, era, era, data)
        by_id = {row['driverId']: row for row in scored}
        self.assertAlmostEqual(by_id['hamilton']['points'], 7)

    def test_own_era_named_fixes_match_official(self):
        import scoring
        data = scoring.load_systems_file()
        for season, kind, row_id in (
            (1951, 'drivers', 'fangio'),
            (1952, 'drivers', 'ascari'),
            (1963, 'drivers', 'hill'),
            (1954, 'drivers', 'fangio'),
            (1954, 'drivers', 'ascari'),
            (2024, 'drivers', 'hamilton'),
            (2024, 'constructors', 'mercedes'),
        ):
            era = scoring.system_for_year(season, data)
            scored = scoring.rescore_season(season, era, 'season', data)
            row = next(item for item in scored[kind] if item['id'] == row_id)
            self.assertAlmostEqual(
                row['points'], row['officialPoints'], places=5,
                msg=f'{season} {row_id}',
            )

    def test_pre_2021_systems_give_sprints_zero(self):
        import scoring
        data = scoring.load_systems_file()
        sprint = {
            'SprintResults': [
                {'position': '1', 'positionText': '1', 'points': '8',
                 'Driver': {'driverId': 'max_verstappen'},
                 'Constructor': {'constructorId': 'red_bull'}},
            ]
        }
        old = scoring.system_for_year(1988, data)
        self.assertEqual(scoring.score_sprint(2023, sprint, old), [])
        new = scoring.system_for_year(2023, data)
        scored = scoring.score_sprint(2023, sprint, new)
        self.assertEqual(scored[0]['points'], 8)

    def test_countback_excludes_sprint_places(self):
        import scoring
        first = scoring.countback_tuple({1: 2, 2: 1})
        second = scoring.countback_tuple({1: 1, 2: 5})
        self.assertLess(first, second)


class PoleRuleTests(unittest.TestCase):
    def test_2021_british_gp_pole_is_verstappen(self):
        from poles import pole_sitter
        race = {'Results': [
            {
                'grid': '1',
                'Driver': {
                    'driverId': 'max_verstappen',
                    'givenName': 'Max', 'familyName': 'Verstappen',
                },
                'Constructor': {'constructorId': 'red_bull'},
            },
            {
                'grid': '2',
                'Driver': {
                    'driverId': 'hamilton',
                    'givenName': 'Lewis', 'familyName': 'Hamilton',
                },
                'Constructor': {'constructorId': 'mercedes'},
            },
        ]}
        qualifying = {'QualifyingResults': [{
            'position': '1',
            'Driver': {
                'driverId': 'hamilton',
                'givenName': 'Lewis', 'familyName': 'Hamilton',
            },
            'Constructor': {'constructorId': 'mercedes'},
        }]}
        pole = pole_sitter(2021, race, qualifying, sprint_weekend=True)
        self.assertEqual(pole['driverId'], 'max_verstappen')
        self.assertEqual(pole['source'], 'grid')

    def test_2022_sprint_weekend_uses_qualifying_p1(self):
        from poles import pole_sitter
        race = {'Results': [{
            'grid': '1',
            'Driver': {
                'driverId': 'russell',
                'givenName': 'George', 'familyName': 'Russell',
            },
            'Constructor': {'constructorId': 'mercedes'},
        }]}
        qualifying = {'QualifyingResults': [{
            'position': '1',
            'Driver': {
                'driverId': 'kevin_magnussen',
                'givenName': 'Kevin', 'familyName': 'Magnussen',
            },
            'Constructor': {'constructorId': 'haas'},
        }]}
        pole = pole_sitter(2022, race, qualifying, sprint_weekend=True)
        self.assertEqual(pole['driverId'], 'kevin_magnussen')
        self.assertEqual(pole['source'], 'qualifying')


class DailyPathAndScoringGuardTests(unittest.TestCase):
    def test_classified_retirement_is_a_dnf(self):
        from drivers_build import did_not_finish
        self.assertTrue(did_not_finish('Retired'))
        self.assertTrue(did_not_finish('Collision'))
        self.assertTrue(did_not_finish('Engine'))
        self.assertFalse(did_not_finish('Finished'))
        self.assertFalse(did_not_finish('+1 Lap'))
        self.assertFalse(did_not_finish('+2 Laps'))

    def test_1952_fl_peels_and_1954_seventh_stays(self):
        import scoring
        self.assertEqual(scoring.peel_fastest_lap_points(0.5), 0.0)
        self.assertAlmostEqual(scoring.peel_fastest_lap_points(1 / 7), 1 / 7)
        self.assertEqual(scoring.peel_fastest_lap_points(1.0), 0.0)
        self.assertEqual(scoring.peel_fastest_lap_points(0.0), 0.0)

    def test_in_progress_is_only_the_current_incomplete_season(self):
        import scoring
        with (
            mock.patch.object(api_update, 'current_year', 2026),
            mock.patch.object(api_update, 'season_is_complete', return_value=False),
        ):
            self.assertTrue(scoring.season_in_progress(2026))
            classified = scoring.classify_own_era_mismatch(
                2026, 'drivers', 'norris', {'ownEraExceptions': []},
            )
            self.assertTrue(classified['documented'])
        with (
            mock.patch.object(api_update, 'current_year', 2026),
            mock.patch.object(api_update, 'season_is_complete', return_value=True),
        ):
            self.assertFalse(scoring.season_in_progress(2026))
            classified = scoring.classify_own_era_mismatch(
                2026, 'drivers', 'norris', {'ownEraExceptions': []},
            )
            self.assertFalse(classified['documented'])
        with (
            mock.patch.object(api_update, 'current_year', 2025),
            mock.patch.object(api_update, 'season_is_complete', return_value=False),
        ):
            self.assertFalse(scoring.season_in_progress(2026))

    def test_rescored_index_keeps_other_seasons(self):
        import scoring
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = os.getcwd()
            os.chdir(temporary_directory)
            try:
                os.makedirs('scoring/rescored')
                with open('scoring/rescored/1950.json', 'w', encoding='utf-8') as handle:
                    json.dump({'season': 1950}, handle)
                with open('scoring/index.json', 'w', encoding='utf-8') as handle:
                    json.dump({'seasons': [1950, 1951]}, handle)
                with (
                    mock.patch.object(scoring, 'load_systems_file', return_value={
                        'systems': [{
                            'id': '2025-onward',
                            'label': '2025 onward',
                            'yearFrom': 2025,
                            'yearTo': None,
                        }],
                    }),
                    mock.patch.object(scoring, 'systems_list', return_value=[{
                        'id': '2025-onward', 'label': '2025 onward',
                    }]),
                    mock.patch.object(scoring, 'system_for_year', return_value={
                        'id': '2025-onward',
                    }),
                    mock.patch.object(scoring, 'rescore_season', return_value={
                        'drivers': [], 'constructors': [],
                    }),
                    mock.patch.object(api_update, 'current_year', 2026),
                    mock.patch.object(api_update, 'season_is_complete', return_value=False),
                ):
                    scoring.write_rescored_files([2025])
                with open('scoring/index.json', encoding='utf-8') as handle:
                    index = json.load(handle)
                self.assertEqual(index['seasons'], [1950, 2025])
                self.assertEqual(index['inProgressSeason']['year'], 2026)
                self.assertFalse(index['inProgressSeason']['complete'])
            finally:
                os.chdir(previous_directory)

    def test_rebuild_does_not_write_when_unexplained(self):
        import drivers_build
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = os.getcwd()
            os.chdir(temporary_directory)
            try:
                os.makedirs('drivers')
                existing = {
                    'driverId': 'alonso',
                    'totalPoles': 22,
                    'poles': {'2024': ['Australian Grand Prix']},
                    'seasonPoles': {'2024': 1},
                }
                with open('drivers/alonso.json', 'w', encoding='utf-8') as handle:
                    json.dump(existing, handle)
                rebuilt = {
                    'alonso': {
                        **existing,
                        'totalPoles': 1,
                        'raceResults': {},
                        'sprintResults': {},
                    }
                }
                with (
                    mock.patch.object(drivers_build, 'collect_pole_diffs', return_value=[]),
                    mock.patch.object(drivers_build, 'build_driver_maps', return_value=rebuilt),
                    mock.patch.object(
                        drivers_build,
                        'shrinking_changes',
                        return_value=([], [{'path': 'totalPoles', 'old': 22, 'new': 1}]),
                    ),
                    mock.patch.object(drivers_build, 'rebuild_drivers_list', return_value=[]),
                ):
                    summary = drivers_build.rebuild_driver_files(write=True)
                self.assertEqual(summary['written'], 0)
                self.assertTrue(summary['unexplained'])
                with open('drivers/alonso.json', encoding='utf-8') as handle:
                    stored = json.load(handle)
                self.assertEqual(stored['totalPoles'], 22)
            finally:
                os.chdir(previous_directory)

    def test_refresh_season_poles_uses_grid_one(self):
        import drivers_build
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = os.getcwd()
            os.chdir(temporary_directory)
            try:
                os.makedirs('races/2024')
                os.makedirs('drivers')
                with open('races/2024/results.json', 'w', encoding='utf-8') as handle:
                    json.dump([{
                        'round': '1',
                        'raceName': 'Bahrain Grand Prix',
                        'Results': [{
                            'grid': '1',
                            'Driver': {
                                'driverId': 'max_verstappen',
                                'givenName': 'Max',
                                'familyName': 'Verstappen',
                            },
                            'Constructor': {'constructorId': 'red_bull'},
                        }],
                    }], handle)
                with open('races/2024/qualifying.json', 'w', encoding='utf-8') as handle:
                    json.dump([{
                        'round': '1',
                        'QualifyingResults': [{
                            'position': '1',
                            'Driver': {
                                'driverId': 'hamilton',
                                'givenName': 'Lewis',
                                'familyName': 'Hamilton',
                            },
                            'Constructor': {'constructorId': 'mercedes'},
                        }],
                    }], handle)
                with open('races/2024/raceDetails.json', 'w', encoding='utf-8') as handle:
                    json.dump([{'round': '1'}], handle)
                with open('drivers/max_verstappen.json', 'w', encoding='utf-8') as handle:
                    json.dump({
                        'driverId': 'max_verstappen',
                        'poles': {'2024': []},
                        'seasonPoles': {'2024': 0},
                        'totalPoles': 0,
                    }, handle)
                with open('drivers/hamilton.json', 'w', encoding='utf-8') as handle:
                    json.dump({
                        'driverId': 'hamilton',
                        'poles': {'2024': ['Bahrain Grand Prix']},
                        'seasonPoles': {'2024': 1},
                        'totalPoles': 1,
                    }, handle)
                drivers_build.refresh_season_poles(2024)
                with open('drivers/max_verstappen.json', encoding='utf-8') as handle:
                    verstappen = json.load(handle)
                with open('drivers/hamilton.json', encoding='utf-8') as handle:
                    hamilton = json.load(handle)
                self.assertEqual(verstappen['poles']['2024'], ['Bahrain Grand Prix'])
                self.assertEqual(hamilton['poles']['2024'], [])
                self.assertEqual(hamilton['seasonPoles']['2024'], 0)
            finally:
                os.chdir(previous_directory)

    def test_daily_update_runs_races_before_drivers(self):
        calls = []

        def record(name):
            def inner(*args, **kwargs):
                calls.append(name)
                if name == 'pre_checks':
                    return True
                if name == 'write_rescored_files':
                    return []
                return None
            return inner

        patches = {
            name: mock.Mock(side_effect=record(name))
            for name in (
                'update_constructors',
                'update_constructor_drivers',
                'pre_checks',
                'update_races',
                'update_raceResults',
                'update_qualifying',
                'update_sprintResults',
                'update_driverStandings',
                'update_constructorStandings',
                'update_driverData',
                'analyse_driverData',
                'replace_NaN',
                'update_team_records',
            )
        }
        with (
            mock.patch.multiple(api_update, **patches),
            mock.patch(
                'drivers_build.refresh_season_poles',
                side_effect=record('refresh_season_poles'),
            ),
            mock.patch(
                'drivers_build.rebuild_drivers_list',
                side_effect=record('rebuild_drivers_list'),
            ),
            mock.patch(
                'scoring.apply_scoring_overrides',
                side_effect=record('apply_scoring_overrides'),
            ),
            mock.patch(
                'scoring.write_rescored_files',
                side_effect=record('write_rescored_files'),
            ),
        ):
            api_update.update()
        self.assertLess(
            calls.index('update_raceResults'),
            calls.index('update_driverData'),
        )
        self.assertLess(
            calls.index('update_driverData'),
            calls.index('analyse_driverData'),
        )
        self.assertIn('rebuild_drivers_list', calls)
        self.assertGreaterEqual(calls.count('refresh_season_poles'), 2)


if __name__ == '__main__':
    unittest.main()
