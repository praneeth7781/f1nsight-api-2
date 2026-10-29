import json
import os
import shutil
import tempfile
import unittest

import circuits_build


def _row(driver_id, position, grid, laps, fastest=None, constructor='team'):
    row = {
        'position': str(position),
        'grid': str(grid),
        'laps': str(laps),
        'Driver': {'driverId': driver_id, 'givenName': driver_id.title(), 'familyName': 'Tester'},
        'Constructor': {'constructorId': constructor, 'name': constructor.title()},
    }
    if fastest:
        row['FastestLap'] = {'rank': '1', 'lap': '10', 'Time': {'time': fastest}}
    return row


def _circuit(circuit_id='ring'):
    return {
        'circuitId': circuit_id,
        'circuitName': f'{circuit_id.title()} Circuit',
        'url': f'https://example.com/{circuit_id}',
        'Location': {'lat': '1.5', 'long': '2.5', 'locality': 'Town', 'country': 'Land'},
    }


def _race(season, round_number, rows, circuit_id='ring', name='Test Grand Prix'):
    return {
        'season': str(season), 'round': str(round_number), 'raceName': name,
        'date': f'{season}-06-01', 'Circuit': _circuit(circuit_id), 'Results': rows,
    }


def _qualifying(season, round_number, rows, circuit_id='ring'):
    return {
        'season': str(season), 'round': str(round_number), 'raceName': 'Test Grand Prix',
        'Circuit': _circuit(circuit_id),
        'QualifyingResults': [
            {'position': str(i + 1), 'Driver': {'driverId': d, 'givenName': d.title(), 'familyName': 'Tester'},
             'Constructor': {'constructorId': 'team', 'name': 'Team'}, **times}
            for i, (d, times) in enumerate(rows)
        ],
    }


class CircuitsBuildTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, path, data):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)

    def season(self, season, results, qualifying=(), details=None):
        self.write(f'races/{season}/results.json', list(results))
        self.write(f'races/{season}/qualifying.json', list(qualifying))
        self.write(f'races/{season}/raceDetails.json', details if details is not None else [
            {k: v for k, v in race.items() if k != 'Results'} for race in results
        ])

    def content(self, data):
        self.write(f"circuits/content/{data['circuitId']}.json", data)

    def test_lap_seconds(self):
        self.assertEqual(circuits_build.lap_seconds('1:12.909'), 72.909)
        self.assertEqual(circuits_build.lap_seconds('59.5'), 59.5)
        self.assertIsNone(circuits_build.lap_seconds(''))
        self.assertIsNone(circuits_build.lap_seconds('DNF'))

    def test_records_only_count_their_layout(self):
        self.season(2005, [_race(2005, 1, [_row('old', 1, 1, 60, '1:20.000')])])
        self.season(2010, [_race(2010, 1, [_row('new', 1, 1, 58, '1:25.000')])])
        self.content({'circuitId': 'ring', 'layouts': [
            {'id': '2010', 'name': 'Current layout', 'from': 2010, 'to': None, 'lengthKm': 5.0},
        ]})
        built = circuits_build.build_all(self.root)['circuits']['ring']
        current, earlier = built['layouts']
        self.assertEqual(current['lapRecord']['driverId'], 'new')
        self.assertEqual(current['lapRecord']['time'], '1:25.000')
        self.assertEqual(earlier['id'], 'earlier')
        self.assertEqual(earlier['lapRecord']['driverId'], 'old')
        self.assertEqual(built['currentLayoutId'], '2010')

    def test_single_round_layout(self):
        self.season(2020, [
            _race(2020, 15, [_row('main', 1, 1, 57, '1:32.000')]),
            _race(2020, 16, [_row('outer', 1, 1, 87, '55.404')]),
        ])
        self.content({'circuitId': 'ring', 'layouts': [
            {'id': 'main', 'name': 'Main', 'from': 2004, 'to': None},
            {'id': 'outer', 'name': 'Outer', 'rounds': [[2020, 16]]},
        ]})
        built = circuits_build.build_all(self.root)['circuits']['ring']
        layouts = {l['id']: l for l in built['layouts']}
        self.assertEqual(layouts['main']['raceCount'], 1)
        self.assertEqual(layouts['outer']['lapRecord']['driverId'], 'outer')
        self.assertEqual([r['layoutId'] for r in built['races']], ['main', 'outer'])

    def test_lap_record_skips_seasons_without_times_and_notes_it(self):
        self.season(2003, [_race(2003, 1, [_row('a', 1, 1, 60)])])
        self.season(2004, [_race(2004, 1, [_row('b', 1, 1, 60, '1:30.000')])])
        built = circuits_build.build_all(self.root)['circuits']['ring']
        layout = built['layouts'][0]
        self.assertEqual(layout['lapRecord']['driverId'], 'b')
        self.assertIn('lapRecord', layout['notes'])
        self.assertIn('lapRecord', built['notes'])

    def test_missing_times_inside_window_are_listed(self):
        self.season(2024, [_race(2024, 3, [_row('a', 1, 1, 57)])])
        built = circuits_build.build_all(self.root)['circuits']['ring']
        self.assertEqual(built['layouts'][0]['missingTimes']['lapRecord'], [[2024, 3]])
        self.assertIn('missingTimes', built['notes'])

    def test_equal_times_keep_the_first(self):
        self.season(2015, [_race(2015, 1, [_row('first', 1, 1, 60, '1:20.000')])])
        self.season(2016, [_race(2016, 1, [_row('second', 1, 1, 60, '1:20.000')])])
        layout = circuits_build.build_all(self.root)['circuits']['ring']['layouts'][0]
        self.assertEqual(layout['lapRecord']['driverId'], 'first')

    def test_pole_record_follows_the_pole_sitter_not_qualifying_p1(self):
        # Fastest qualifier took a grid penalty: the lap didn't win pole.
        self.season(2019, [_race(2019, 1, [
            _row('pole', 1, 1, 71), _row('penalised', 2, 4, 71),
        ])], qualifying=[_qualifying(2019, 1, [
            ('penalised', {'Q1': '1:16.000', 'Q2': '1:15.500', 'Q3': '1:14.758'}),
            ('pole', {'Q1': '1:16.100', 'Q2': '1:15.600', 'Q3': '1:15.024'}),
        ])])
        layout = circuits_build.build_all(self.root)['circuits']['ring']['layouts'][0]
        self.assertEqual(layout['poleRecord']['driverId'], 'pole')
        self.assertEqual(layout['poleRecord']['time'], '1:15.024')
        self.assertEqual(layout['poleRecord']['session'], 'Q3')

    def test_race_laps_ignore_shortened_races(self):
        self.season(2019, [_race(2019, 1, [_row('a', 1, 1, 44)])])
        self.season(2020, [_race(2020, 1, [_row('b', 1, 1, 44)])])
        self.season(2021, [_race(2021, 1, [_row('c', 1, 1, 1)])])
        layout = circuits_build.build_all(self.root)['circuits']['ring']['layouts'][0]
        self.assertEqual(layout['raceLaps'], 44)

    def test_most_wins_keeps_ties_at_the_cutoff(self):
        races = [_race(2000 + i, 1, [_row(d, 1, 1, 50)]) for i, d in enumerate('abcdeff')]
        for race in races:
            self.season(int(race['season']), [race])
        wins = circuits_build.build_all(self.root)['circuits']['ring']['championship']['mostWins']
        self.assertEqual(wins[0], {'driverId': 'f', 'name': 'F Tester', 'count': 2})
        self.assertEqual(len(wins), 6)

    def test_indy_500_is_left_out_of_championship_stats(self):
        self.season(1955, [_race(1955, 2, [_row('indy', 1, 1, 200)], name='Indianapolis 500')])
        self.season(2005, [_race(2005, 9, [_row('gp', 1, 1, 73)])])
        built = circuits_build.build_all(self.root)['circuits']['ring']
        self.assertEqual(built['championship']['raceCount'], 1)
        self.assertEqual(built['championship']['firstSeason'], 2005)
        self.assertEqual(built['championship']['mostWins'][0]['driverId'], 'gp')

    def test_status_and_next_race(self):
        held = _race(2026, 1, [_row('a', 1, 1, 58)], circuit_id='ring')
        details = [
            {k: v for k, v in held.items() if k != 'Results'},
            {'season': '2026', 'round': '2', 'raceName': 'New Grand Prix', 'date': '2026-07-01',
             'time': '13:00:00Z', 'Circuit': _circuit('fresh')},
        ]
        self.season(2026, [held], details=details)
        self.season(2010, [_race(2010, 1, [_row('b', 1, 1, 58)], circuit_id='gone')])
        built = circuits_build.build_all(self.root)
        circuits = built['circuits']
        self.assertEqual(circuits['ring']['status'], 'current')
        self.assertIsNone(circuits['ring']['nextRace'])
        self.assertEqual(circuits['fresh']['status'], 'current')
        self.assertEqual(circuits['fresh']['nextRace']['round'], 2)
        self.assertEqual(circuits['fresh']['name'], 'Fresh Circuit')
        self.assertEqual(circuits['gone']['status'], 'historical')
        self.assertEqual(built['index']['circuits'][0]['circuitId'], 'fresh')
        self.assertEqual(built['index']['dataThrough']['season'], 2026)

    def test_retired_content_overrides_a_recent_race(self):
        self.season(2025, [_race(2025, 1, [_row('a', 1, 1, 58)])])
        self.season(2026, [], details=[
            {'season': '2026', 'round': '1', 'raceName': 'Other Grand Prix', 'date': '2026-03-01',
             'Circuit': _circuit('other')},
        ])
        self.assertEqual(circuits_build.build_all(self.root)['circuits']['ring']['status'], 'current')
        self.content({'circuitId': 'ring', 'retired': True, 'layouts': []})
        self.assertEqual(circuits_build.build_all(self.root)['circuits']['ring']['status'], 'historical')

    def test_overlapping_layouts_are_rejected(self):
        self.season(2020, [_race(2020, 1, [_row('a', 1, 1, 58)])])
        self.content({'circuitId': 'ring', 'layouts': [
            {'id': 'a', 'from': 2000, 'to': 2010},
            {'id': 'b', 'from': 2010, 'to': None},
        ]})
        with self.assertRaises(RuntimeError):
            circuits_build.publish_circuits(write=False, root=self.root)

    def test_sources_are_grouped_by_url(self):
        shared = {'title': 'Guide', 'url': 'https://example.com/guide', 'publisher': 'Example'}
        self.content({'circuitId': 'one', 'layouts': [], 'sources': [shared],
                      'moments': [{'season': 2000, 'title': 'Moment', 'video': 'https://youtu.be/x'}]})
        self.content({'circuitId': 'two', 'layouts': [], 'sources': [shared]})
        sources = circuits_build.build_all(self.root)['sources']
        self.assertEqual(sources['sources'][0]['circuits'], ['one', 'two'])
        self.assertEqual(sources['videos'][0]['circuitId'], 'one')

    def test_warns_when_the_latest_race_breaks_the_lap_count(self):
        for season in (2022, 2023):
            self.season(season, [_race(season, 1, [_row('a', 1, 1, 52, '1:30.000')])])
        self.season(2024, [_race(2024, 1, [_row('b', 1, 1, 60, '1:28.000')])])
        warnings = [w for w in circuits_build.build_all(self.root)['warnings'] if 'laps' in w]
        self.assertEqual(len(warnings), 1)
        self.assertIn('did 60 laps', warnings[0])
        self.assertIn('usually run 52', warnings[0])

    def test_lap_count_warning_only_checks_the_latest_race(self):
        self.season(2022, [_race(2022, 1, [_row('a', 1, 1, 52, '1:30.000')])])
        self.season(2023, [
            _race(2023, 1, [_row('a', 1, 1, 52, '1:30.000')]),
            _race(2023, 2, [_row('b', 1, 1, 30, '1:30.000')], circuit_id='other'),
        ])
        self.season(2024, [_race(2024, 1, [_row('c', 1, 1, 52, '1:29.000')])])
        warnings = circuits_build.build_all(self.root)['warnings']
        self.assertEqual([w for w in warnings if 'laps' in w], [])

    def test_warns_when_the_latest_race_has_no_times(self):
        self.season(2024, [_race(2024, 1, [_row('a', 1, 1, 58)])], qualifying=[
            _qualifying(2024, 1, [('a', {'Q1': '', 'Q2': '', 'Q3': ''})]),
        ])
        warnings = circuits_build.build_all(self.root)['warnings']
        self.assertTrue(any('no fastest-lap time' in w for w in warnings))
        self.assertTrue(any('no pole lap time' in w for w in warnings))

    def test_warns_about_calendar_circuits_without_a_guide(self):
        self.season(2026, [], details=[
            {'season': '2026', 'round': '1', 'raceName': 'New Grand Prix', 'date': '2026-03-01',
             'Circuit': _circuit('fresh')},
        ])
        warnings = circuits_build.publish_circuits(write=False, root=self.root)['warnings']
        self.assertEqual(len(warnings), 1)
        self.assertIn('circuits/content/fresh.json', warnings[0])

    def test_publish_writes_only_changed_files(self):
        self.season(2024, [_race(2024, 1, [_row('a', 1, 1, 58, '1:20.000')])])
        first = circuits_build.publish_circuits(write=True, root=self.root)
        second = circuits_build.publish_circuits(write=True, root=self.root)
        self.assertGreater(first['filesWritten'], 0)
        self.assertEqual(second['filesWritten'], 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'circuits', 'ring.json')))


if __name__ == '__main__':
    unittest.main()
