import json
import os
import shutil
import tempfile
import unittest

import drivers_v2
import outcomes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _driver(driver_id):
    return {
        'driverId': driver_id,
        'givenName': driver_id.title(),
        'familyName': 'Tester',
        'code': driver_id[:3].upper(),
    }


def _result(driver_id, constructor_id, position, text, status, grid, laps,
            points='0', fastest_rank=None):
    result = {
        'number': '1',
        'position': position,
        'positionText': text,
        'points': points,
        'Driver': _driver(driver_id),
        'Constructor': {'constructorId': constructor_id},
        'grid': grid,
        'laps': laps,
        'status': status,
    }
    if fastest_rank is not None:
        result['FastestLap'] = {'rank': fastest_rank, 'Time': {'time': '1:30.000'}}
    return result


def _standing(position, driver_id, points):
    return {
        'position': position,
        'points': points,
        'Driver': _driver(driver_id),
        'Constructors': [{'constructorId': 'red'}],
    }


def write_archive():
    """A two-race 2025 season covering every outcome the site needs."""
    shutil.copy(os.path.join(ROOT, 'statusCategories.json'), 'statusCategories.json')
    with open('retirementOverrides.json', 'w', encoding='utf-8') as handle:
        json.dump({'overrides': [
            {'season': 2025, 'round': 2, 'driverId': 'ada', 'cause': 'contact'},
        ]}, handle)
    os.makedirs('races/2025')
    os.makedirs('drivers')
    with open('driversList.json', 'w', encoding='utf-8') as handle:
        json.dump([
            {**_driver(driver_id), 'dateOfBirth': '2000-01-01', 'nationality': 'Testish'}
            for driver_id in ('ada', 'bob', 'cy', 'dee', 'eve')
        ], handle)
    races = [
        {
            'season': '2025', 'round': '1', 'raceName': 'Alpha Grand Prix',
            'Circuit': {'circuitId': 'alpha'}, 'date': '2025-03-01',
            'Results': [
                _result('ada', 'red', '1', '1', 'Finished', '1', '57', '25', fastest_rank='1'),
                _result('bob', 'red', '2', '2', 'Finished', '0', '57', '18'),
                _result('dee', 'blue', '3', '3', '+1 Lap', '4', '56', '15'),
                _result('cy', 'blue', '4', 'R', 'Engine', '3', '20'),
                _result('eve', 'green', '5', 'F', 'Did not qualify', '0', '0'),
            ],
        },
        {
            'season': '2025', 'round': '2', 'raceName': 'Beta Grand Prix',
            'Circuit': {'circuitId': 'beta'}, 'date': '2025-03-15',
            'Results': [
                _result('bob', 'red', '1', '1', 'Finished', '1', '50', '25'),
                _result('dee', 'blue', '2', '2', 'Finished', '3', '50', '18'),
                _result('ada', 'red', '3', 'R', 'Retired', '2', '10'),
                _result('cy', 'blue', '4', 'D', 'Disqualified', '5', '50'),
            ],
        },
    ]
    with open('races/2025/results.json', 'w', encoding='utf-8') as handle:
        json.dump(races, handle)
    with open('races/2025/qualifying.json', 'w', encoding='utf-8') as handle:
        json.dump([
            {'round': '1', 'QualifyingResults': [
                {'position': '1', 'Driver': _driver('ada'), 'Q1': '1:31.0', 'Q2': '1:30.5', 'Q3': '1:30.0'},
                {'position': '2', 'Driver': _driver('bob'), 'Q1': '1:31.1', 'Q2': '1:30.6', 'Q3': '1:30.1'},
            ]},
            {'round': '2', 'QualifyingResults': [
                {'position': '1', 'Driver': _driver('bob'), 'Q1': '1:21.0', 'Q2': '1:20.5', 'Q3': '1:20.0'},
            ]},
        ], handle)
    with open('races/2025/sprint.json', 'w', encoding='utf-8') as handle:
        json.dump([{'round': '2', 'SprintResults': [
            {'position': '1', 'positionText': '1', 'points': '8', 'status': 'Finished',
             'Driver': _driver('ada'), 'Constructor': {'constructorId': 'red'}},
        ]}], handle)
    with open('races/2025/raceDetails.json', 'w', encoding='utf-8') as handle:
        json.dump([{'round': '1'}, {'round': '2', 'Sprint': {'date': '2025-03-14'}}], handle)
    with open('races/2025/driverStandings.json', 'w', encoding='utf-8') as handle:
        json.dump({
            '1': [_standing('1', 'ada', '25'), _standing('2', 'bob', '18'), _standing('3', 'dee', '15')],
            '2': [_standing('1', 'bob', '43'), _standing('2', 'dee', '33'), _standing('3', 'ada', '33')],
        }, handle)
    with open('races/2025/constructorStandings.json', 'w', encoding='utf-8') as handle:
        json.dump({'2': [
            {'position': '1', 'points': '76', 'Constructor': {'constructorId': 'red'}},
            {'position': '2', 'points': '33', 'Constructor': {'constructorId': 'blue'}},
            {'position': '3', 'points': '0', 'Constructor': {'constructorId': 'green'}},
        ]}, handle)


class SyntheticArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        outcomes.reset_cache()
        write_archive()
        self.rows, self.coverage = drivers_v2.build_rows()

    def tearDown(self):
        os.chdir(self.previous_directory)
        outcomes.reset_cache()
        self.directory.cleanup()

    def row(self, driver_id, round_number):
        return next(row for row in self.rows[driver_id] if row['round'] == round_number)

    def test_rows_validate(self):
        for driver_id, rows in self.rows.items():
            self.assertEqual(drivers_v2.validate_rows(driver_id, rows), [])

    def test_pit_lane_start_is_not_grid_zero(self):
        row = self.row('bob', 1)
        self.assertIsNone(row['grid'])
        self.assertTrue(row['pitLaneStart'])
        self.assertEqual(row['qualifying']['position'], 2)
        self.assertEqual(self.row('bob', 2)['grid'], 1)
        self.assertTrue(self.row('bob', 2)['pole'])
        self.assertFalse(self.row('bob', 2)['pitLaneStart'])

    def test_retirement_override_and_teammates(self):
        row = self.row('ada', 2)
        self.assertEqual(row['outcome'], 'retired')
        self.assertEqual(row['cause'], 'contact')
        self.assertIsNone(row['finish'])
        self.assertFalse(row['classified'])
        self.assertEqual(row['laps'], 10)
        self.assertEqual(row['raceLaps'], 50)
        self.assertEqual(row['teammates'], [
            {'driverId': 'bob', 'finish': 1, 'outcome': 'finished', 'grid': 1},
        ])
        self.assertEqual(row['sprint'], {
            'finish': 1, 'positionText': '1', 'outcome': 'finished', 'points': 8,
        })
        self.assertEqual(row['standingAfter'], {'position': 3, 'points': 33})

    def test_dnq_is_not_a_start_and_dsq_is_not_a_dnf(self):
        eve = self.row('eve', 1)
        self.assertEqual(eve['outcome'], 'dnq')
        self.assertIsNone(eve['finish'])
        self.assertIsNone(eve['grid'])
        self.assertEqual(eve['teammates'], [])
        cy = self.row('cy', 2)
        self.assertEqual(cy['outcome'], 'dsq')
        self.assertIsNone(cy['cause'])
        self.assertIsNone(cy['finish'])
        self.assertEqual(self.row('cy', 1)['cause'], 'mechanical')
        self.assertEqual(self.row('dee', 1)['outcome'], 'lapped')
        self.assertEqual(self.row('dee', 1)['finish'], 3)

    def test_summary_tallies(self):
        summary = drivers_v2.build_summary(
            'ada', self.rows['ada'], drivers_v2._identity_map()['ada'], self.coverage
        )
        career = summary['career']
        self.assertEqual(career['entries'], 2)
        self.assertEqual(career['starts'], 2)
        self.assertEqual(career['wins'], 1)
        self.assertEqual(career['podiums'], 1)
        self.assertEqual(career['poles'], 1)
        self.assertEqual(career['fastestLaps'], 1)
        self.assertEqual(career['points'], 25)
        self.assertEqual(career['sprintPoints'], 8)
        self.assertEqual(career['dnfs'], 1)
        self.assertEqual(career['dnfsByCause']['contact'], 1)
        self.assertEqual(career['avgFinish'], 1.0)
        self.assertEqual(career['avgGrid'], 1.5)
        season = summary['seasons']['2025']
        self.assertEqual(season['constructorIds'], ['red'])
        self.assertEqual(season['constructorStandings'], {'red': 1})
        self.assertEqual(season['standing'], {'position': 3, 'points': 33})
        self.assertEqual(season['teammateIds'], ['bob'])
        self.assertEqual(summary['code'], 'ADA')
        self.assertEqual(summary['nationality'], 'Testish')

        eve = drivers_v2.build_summary('eve', self.rows['eve'], {}, self.coverage)['career']
        self.assertEqual((eve['entries'], eve['starts'], eve['dnq']), (1, 0, 1))
        self.assertIsNone(eve['avgFinish'])
        cy = drivers_v2.build_summary('cy', self.rows['cy'], {}, self.coverage)['career']
        self.assertEqual((cy['starts'], cy['dnfs'], cy['dsq']), (2, 1, 1))
        self.assertEqual(cy['dnfsByCause']['mechanical'], 1)

    def test_coverage_counts_causes(self):
        season = self.coverage['2025']
        self.assertEqual(season['races'], 2)
        self.assertTrue(season['hasQualifying'])
        self.assertTrue(season['hasSprints'])
        self.assertEqual(season['retired'], 2)
        self.assertEqual(season['retiredCauseKnown'], 2)
        self.assertEqual(season['unknownStatuses'], [])
        self.assertEqual(season['constructorStandings'], {'red': 1, 'blue': 2, 'green': 3})

    def test_publish_writes_once_and_validates(self):
        report = drivers_v2.publish_v2(write=True)
        self.assertEqual(report['drivers'], 5)
        self.assertEqual(report['rows'], 9)
        self.assertEqual(report['unknownRetirements'], 0)
        with open('v2/drivers/ada/results.json', encoding='utf-8') as handle:
            document = json.load(handle)
        self.assertEqual(document['driverId'], 'ada')
        self.assertEqual(document['results'], self.rows['ada'])
        self.assertTrue(os.path.exists('v2/drivers/ada/summary.json'))
        with open('v2/unknownRetirements.json', encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['retirements'], [])
        with open('v2/index.json', encoding='utf-8') as handle:
            index = json.load(handle)
        self.assertEqual(index['schemaVersion'], drivers_v2.SCHEMA_VERSION)
        self.assertIn('mechanical', index['causes'])
        # A second run rewrites only index.json (it carries the timestamp).
        self.assertEqual(drivers_v2.publish_v2(write=True)['written'], 1)

    def test_unknown_retirements_worklist(self):
        with open('retirementOverrides.json', 'w', encoding='utf-8') as handle:
            json.dump({'overrides': []}, handle)
        outcomes.reset_cache()
        rows, _ = drivers_v2.build_rows()
        worklist = drivers_v2.unknown_retirements(rows)
        self.assertEqual(len(worklist), 1)
        self.assertEqual((worklist[0]['driverId'], worklist[0]['round']), ('ada', 2))

    def test_validation_rejects_bad_rows(self):
        row = dict(self.rows['ada'][0])
        row['grid'] = 0
        row['outcome'] = 'teleported'
        problems = drivers_v2.validate_rows('ada', [row])
        self.assertTrue(any('grid 0' in problem for problem in problems))
        self.assertTrue(any('bad outcome' in problem for problem in problems))


@unittest.skipUnless(os.path.isdir(os.path.join(ROOT, 'races')), 'needs the race archive')
class ArchiveRegressionTests(unittest.TestCase):
    """Known facts the published data must keep reproducing."""

    @classmethod
    def setUpClass(cls):
        cls.previous_directory = os.getcwd()
        os.chdir(ROOT)
        outcomes.reset_cache()
        cls.rows, cls.coverage = drivers_v2.build_rows()

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.previous_directory)
        outcomes.reset_cache()

    def test_norris_2025_non_finishes(self):
        rows = [row for row in self.rows['norris'] if row['season'] == 2025]
        by_outcome = {}
        for row in rows:
            by_outcome.setdefault(row['outcome'], []).append(row)
        self.assertEqual(len(by_outcome.get('retired', [])), 2)
        self.assertEqual(len(by_outcome.get('dsq', [])), 1)
        causes = {row['round']: row['cause'] for row in by_outcome['retired']}
        self.assertEqual(causes, {10: 'contact', 15: 'mechanical'})

    def test_hamilton_totals_through_2025(self):
        rows = [row for row in self.rows['hamilton'] if row['season'] <= 2025]
        totals = drivers_v2.tally(rows)
        self.assertEqual(totals['wins'], 105)
        self.assertEqual(totals['poles'], 104)
        self.assertEqual(totals['podiums'], 202)

    def test_every_row_validates(self):
        for driver_id, rows in self.rows.items():
            self.assertEqual(drivers_v2.validate_rows(driver_id, rows), [], driver_id)

    def test_lapped_and_dnq_rows_are_not_dnfs(self):
        # 'Withdrew' with an R or a finishing position is a driver who started
        # and then stopped, so only the W/F rows must be non-starts.
        for rows in self.rows.values():
            for row in rows:
                if row['status'] == 'Did not qualify':
                    self.assertEqual(row['outcome'], 'dnq', row)
                if row['status'] == 'Lapped' and row['positionText'].isdigit():
                    self.assertEqual(row['outcome'], 'lapped', row)
                if row['positionText'] in ('W', 'F'):
                    self.assertIn(row['outcome'], ('dns', 'dnq', 'withdrew'), row)
                    self.assertIsNone(row['finish'])
                if row['outcome'] == 'retired':
                    self.assertIsNotNone(row['cause'])


if __name__ == '__main__':
    unittest.main()
