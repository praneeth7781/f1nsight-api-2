import json
import os
import shutil
import tempfile
import unittest

import drivers_build
import outcomes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AnalyseDriverTests(unittest.TestCase):
    def setUp(self):
        self.previous_directory = os.getcwd()
        os.chdir(ROOT)
        outcomes.reset_cache()

    def tearDown(self):
        os.chdir(self.previous_directory)

    def test_rates_use_starts_and_skip_grid_zero(self):
        data = {
            'driverId': 'tester',
            'totalWins': 1, 'totalPodiums': 1, 'totalPoles': 1, 'totalDNFs': 0,
            'seasonWins': {'2025': 1},
            'seasonPodiums': {'2025': 1},
            'seasonPoles': {'2025': 1},
            'seasonDNFs': {'2025': 0},
            'poles': {'2025': ['Alpha Grand Prix']},
            'finalStandings': {'2025': {'year': '2025', 'position': '5', 'points': '31'}},
            'racePosition': {'2025': {'year': '2025', 'positions': {
                'Alpha Grand Prix': '1', 'Beta Grand Prix': '7', 'Gamma Grand Prix': '20',
            }}},
            'qualiPosition': {'2025': {'year': '2025', 'positions': {
                'Alpha Grand Prix': '1', 'Beta Grand Prix': '0', 'Gamma Grand Prix': '12',
            }}},
            'raceResults': {'2025': {
                '1': {'raceName': 'Alpha Grand Prix', 'positionText': '1', 'status': 'Finished'},
                '2': {'raceName': 'Beta Grand Prix', 'positionText': '7', 'status': 'Finished'},
                '3': {'raceName': 'Gamma Grand Prix', 'positionText': 'F', 'status': 'Did not qualify'},
            }},
        }
        drivers_build.analyse_driver(data)
        self.assertEqual(data['seasonStarts'], {'2025': 2})
        self.assertEqual(data['totalStarts'], 2)
        self.assertEqual(data['rates']['wins']['2025'], 0.5)
        self.assertEqual(data['winRate'], 0.5)
        self.assertEqual(data['avgRacePositions']['2025'], 4.0)
        self.assertEqual(data['avgQualiPositions']['2025'], 1.0)
        self.assertEqual(data['positionsGainLost']['2025'], {'Alpha Grand Prix': 0})
        self.assertEqual(data['ptwConRate']['2025'], 1.0)
        self.assertEqual(data['peakSeason']['wins'], {'season': '2025', 'wins': 1})
        self.assertEqual(data['consistency']['mean']['points'], 31.0)

    def test_empty_payload_does_not_crash(self):
        data = {'driverId': 'ghost', 'raceResults': {}, 'sprintResults': {}}
        drivers_build.analyse_driver(data)
        self.assertEqual(data['totalStarts'], 0)
        self.assertEqual(data['winRate'], 0.0)
        self.assertEqual(data['peakSeason']['wins']['season'], 'No peak season')


class DnfCountingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        outcomes.reset_cache()
        shutil.copy(os.path.join(ROOT, 'statusCategories.json'), 'statusCategories.json')
        os.makedirs('races/2025')
        os.makedirs('drivers')

        def result(position, text, status, grid='3'):
            return {
                'position': position, 'positionText': text, 'status': status,
                'grid': grid, 'laps': '50', 'points': '0',
                'Driver': {'driverId': 'tester', 'givenName': 'Tess', 'familyName': 'Tester'},
                'Constructor': {'constructorId': 'red'},
            }

        races = [
            {'season': '2025', 'round': str(index + 1), 'raceName': name,
             'Results': [result(*args)]}
            for index, (name, args) in enumerate((
                ('Lapped Grand Prix', ('12', '12', 'Lapped')),
                ('Dnq Grand Prix', ('25', 'F', 'Did not qualify', '0')),
                ('Dsq Grand Prix', ('20', 'D', 'Disqualified')),
                ('Classified Retirement Grand Prix', ('9', '9', 'Engine')),
                ('Retired Grand Prix', ('18', 'R', 'Retired')),
                ('Withdrawn Grand Prix', ('22', 'W', 'Withdrew')),
            ))
        ]
        with open('races/2025/results.json', 'w', encoding='utf-8') as handle:
            json.dump(races, handle)

    def tearDown(self):
        os.chdir(self.previous_directory)
        outcomes.reset_cache()
        self.directory.cleanup()

    def test_only_retirements_count_as_dnfs(self):
        data = drivers_build.build_driver_maps()['tester']
        self.assertEqual(sorted(data['DNFs']['2025']), [
            'Classified Retirement Grand Prix', 'Retired Grand Prix',
        ])
        self.assertEqual(data['seasonDNFs']['2025'], 2)
        self.assertEqual(data['totalDNFs'], 2)
        drivers_build.analyse_driver(data)
        # Lapped, DSQ and the two retirements started; DNQ and withdrawal did not.
        self.assertEqual(data['seasonStarts']['2025'], 4)
        self.assertEqual(data['rates']['DNFs']['2025'], 0.5)

    def test_rebuild_skips_unchanged_files(self):
        with open('driversList.json', 'w', encoding='utf-8') as handle:
            json.dump([{'driverId': 'ghost', 'givenName': 'No', 'familyName': 'Rows'}], handle)
        first = drivers_build.rebuild_driver_files(write=True)
        self.assertEqual(first['written'], 1)
        self.assertEqual(first['orphanFiles'], [])
        # A list entry with no driver file or race rows is dropped.
        with open('driversList.json', encoding='utf-8') as handle:
            self.assertEqual([row['driverId'] for row in json.load(handle)], ['tester'])
        with open('drivers/tester.json', encoding='utf-8') as handle:
            stored = json.load(handle)
        self.assertTrue(stored['lastUpdate'])
        second = drivers_build.rebuild_driver_files(write=True)
        self.assertEqual((second['written'], second['unchanged']), (0, 1))
        with open('drivers/tester.json', encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['lastUpdate'], stored['lastUpdate'])


if __name__ == '__main__':
    unittest.main()
