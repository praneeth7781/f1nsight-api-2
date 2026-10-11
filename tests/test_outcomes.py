import json
import os
import unittest

import outcomes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.previous_directory = os.getcwd()
        os.chdir(ROOT)
        outcomes.reset_cache()

    def tearDown(self):
        os.chdir(self.previous_directory)
        outcomes.reset_cache()

    def classify(self, position_text, status, **extra):
        return outcomes.classify(
            {'positionText': position_text, 'status': status, **extra}
        )

    def test_finished_and_lapped_are_classified_starts(self):
        cases = (
            ('1', 'Finished', 'finished'),
            ('12', '+1 Lap', 'lapped'),
            ('15', '+3 Laps', 'lapped'),
            ('12', 'Lapped', 'lapped'),
        )
        for text, status, outcome in cases:
            labels = self.classify(text, status)
            self.assertEqual(labels['outcome'], outcome, status)
            self.assertTrue(labels['started'])
            self.assertTrue(labels['classified'])
            self.assertFalse(labels['dnf'])
            self.assertIsNone(labels['cause'])

    def test_retirements_are_dnfs_with_causes(self):
        cases = (
            ('R', 'Engine', 'mechanical'),
            ('R', 'Power Unit', 'mechanical'),
            ('R', 'Accident', 'accident'),
            ('R', 'Spun off', 'accident'),
            ('R', 'Collision', 'contact'),
            ('R', 'Collision damage', 'contact'),
            ('R', 'Puncture', 'tyre'),
            ('R', 'Out of fuel', 'operational'),
            ('R', 'Driver unwell', 'driver'),
            ('R', 'Retired', 'unknown'),
            ('N', 'Not classified', 'unknown'),
        )
        for text, status, cause in cases:
            labels = self.classify(text, status)
            self.assertEqual(labels['outcome'], 'retired', status)
            self.assertEqual(labels['cause'], cause, status)
            self.assertTrue(labels['dnf'])
            self.assertTrue(labels['started'])
            self.assertFalse(labels['classified'])

    def test_classified_retirement_is_still_a_dnf(self):
        labels = self.classify('18', 'Retired')
        self.assertEqual(labels['outcome'], 'retired')
        self.assertTrue(labels['classified'])
        self.assertTrue(labels['dnf'])
        self.assertEqual(labels['cause'], 'unknown')
        labels = self.classify('9', 'Engine')
        self.assertEqual((labels['outcome'], labels['cause']), ('retired', 'mechanical'))

    def test_non_starts_are_neither_starts_nor_dnfs(self):
        cases = (
            ('F', 'Did not qualify', 'dnq', None),
            ('F', '107% Rule', 'dnq', None),
            ('F', 'Did not prequalify', 'dnq', None),
            ('W', 'Withdrew', 'withdrew', None),
            ('W', 'Did not start', 'dns', None),
            ('W', 'Engine', 'withdrew', 'mechanical'),
        )
        for text, status, outcome, cause in cases:
            labels = self.classify(text, status)
            self.assertEqual(labels['outcome'], outcome, status)
            self.assertEqual(labels['cause'], cause, status)
            self.assertFalse(labels['started'])
            self.assertFalse(labels['dnf'])
            self.assertFalse(labels['classified'])

    def test_disqualified_started_but_is_not_a_dnf(self):
        for text, status in (('D', 'Disqualified'), ('E', 'Excluded'), ('D', 'Accident')):
            labels = self.classify(text, status)
            self.assertEqual(labels['outcome'], 'dsq', status)
            self.assertTrue(labels['started'])
            self.assertFalse(labels['dnf'])
            self.assertFalse(labels['classified'])

    def test_unknown_status_is_an_unknown_retirement(self):
        labels = self.classify('R', 'Flux capacitor')
        self.assertEqual((labels['outcome'], labels['cause']), ('retired', 'unknown'))
        self.assertFalse(labels['statusKnown'])
        # A classified row with an unknown status still finished the race.
        self.assertTrue(self.classify('7', 'Flux capacitor')['dnf'])

    def test_missing_position_text_falls_back_to_status(self):
        self.assertEqual(self.classify('', 'Finished')['outcome'], 'finished')
        self.assertTrue(self.classify('', 'Finished')['classified'])
        self.assertEqual(self.classify('', 'Gearbox')['outcome'], 'retired')
        self.assertEqual(self.classify('', 'Did not qualify')['outcome'], 'dnq')

    def test_retirement_override_supplies_cause(self):
        outcomes._cache['overrides'] = {
            ('2025', '10', 'norris'): {'cause': 'contact'},
        }
        result = {
            'positionText': '18',
            'status': 'Retired',
            'Driver': {'driverId': 'norris'},
        }
        self.assertEqual(outcomes.classify(result, 2025, 10)['cause'], 'contact')
        self.assertEqual(outcomes.classify(result, 2025, 11)['cause'], 'unknown')
        self.assertEqual(outcomes.classify(result)['cause'], 'unknown')

    def test_status_only_dnf_check(self):
        for status in ('Retired', 'Collision', 'Engine', 'Spun off'):
            self.assertTrue(outcomes.did_not_finish(status), status)
        for status in (
            'Finished', '+1 Lap', '+2 Laps', 'Lapped', 'Did not qualify',
            'Disqualified', 'Withdrew', 'Did not start',
        ):
            self.assertFalse(outcomes.did_not_finish(status), status)

    def test_vocabulary_matches_the_status_file(self):
        with open(outcomes.STATUS_FILE, encoding='utf-8') as handle:
            data = json.load(handle)
        self.assertEqual(set(data['outcomes']), set(outcomes.OUTCOMES))
        self.assertEqual(set(data['causes']), set(outcomes.CAUSES))
        self.assertTrue(set(data['kinds']) <= set(outcomes.OUTCOMES))
        self.assertEqual(set(data['retirements']), set(outcomes.CAUSES))
        with open(outcomes.RETIREMENT_OVERRIDES_FILE, encoding='utf-8') as handle:
            overrides = json.load(handle)['overrides']
        for row in overrides:
            self.assertIn(row['cause'], outcomes.CAUSES)
        # Loading raises when a status appears in two groups.
        outcomes.load_status_categories()

    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, 'races')), 'needs the race archive')
    def test_every_status_in_the_archive_is_listed(self):
        unknown = set()
        for season in os.listdir('races'):
            path = os.path.join('races', season, 'results.json')
            if not season.isdigit() or not os.path.exists(path):
                continue
            with open(path, encoding='utf-8') as handle:
                races = json.load(handle)
            for race in races:
                for result in race.get('Results') or []:
                    if not outcomes.status_info(result.get('status'))[2]:
                        unknown.add(result.get('status'))
        self.assertEqual(sorted(unknown), [])


if __name__ == '__main__':
    unittest.main()
