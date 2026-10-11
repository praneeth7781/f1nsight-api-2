"""Publish v2 driver data: one labelled row per driver per race.

The site computes comparison stats from these rows with whatever adjustments
the user switches on (points system, mechanical DNFs, teammate-relative,
per-start rates), so the files hold facts and labels, not opinions.

v2/drivers/{driverId}/results.json   rows, each with outcome, cause, teammates
v2/drivers/{driverId}/summary.json   as-recorded totals per career and season
v2/coverage.json                     what each season's data can support
v2/unknownRetirements.json           retirements still missing a cause
v2/index.json                        schema version and vocabulary
"""

from __future__ import annotations

import json
import os
from collections import defaultdict

import api_update
import outcomes
from poles import is_sprint_weekend, pole_sitter
from scoring import fastest_lap_rank, format_points, is_indy_500, parse_points

SCHEMA_VERSION = 2
V2_DIR = 'v2'
# Ergast documents grid 0 as a pit-lane start, but before the 2000s it mostly
# means the grid slot was never recorded.
PIT_LANE_GRID_FROM = 2000

ROW_TYPES = {
    'season': int, 'round': int, 'raceName': str, 'circuitId': (str, type(None)),
    'date': (str, type(None)), 'indy500': bool, 'constructorId': (str, type(None)),
    'grid': (int, type(None)), 'pitLaneStart': (bool, type(None)),
    'qualifying': (dict, type(None)), 'finish': (int, type(None)),
    'positionText': str, 'classified': bool, 'outcome': str,
    'cause': (str, type(None)), 'status': str, 'laps': (int, type(None)),
    'raceLaps': (int, type(None)), 'points': (int, float),
    'fastestLapRank': (int, type(None)), 'pole': bool, 'sharedDrive': bool,
    'sprint': (dict, type(None)), 'standingAfter': (dict, type(None)),
    'teammates': list,
}


def _int(value):
    text = str(value if value is not None else '')
    return int(text) if text.isdigit() else None


def _by_round(path):
    return {
        str(race.get('round')): race
        for race in api_update.load_json(path, [])
    }


def _final_positions(season, kind):
    rows = api_update._final_standings(season, kind)
    key = 'Driver' if kind == 'driverStandings' else 'Constructor'
    positions = {}
    for row in rows:
        entry_id = (row.get(key) or {}).get(f'{key[0].lower()}{key[1:]}Id')
        if entry_id:
            positions[entry_id] = {
                'position': _int(row.get('position')),
                'points': format_points(parse_points(row.get('points'))),
            }
    return positions


def _best_row_per_driver(rows):
    """Shared drives give one driver several rows; keep the best one."""
    best = {}
    for result in rows:
        driver_id = (result.get('Driver') or {}).get('driverId')
        if not driver_id:
            continue
        current = best.get(driver_id)
        if current is None:
            best[driver_id] = result
            continue
        points = parse_points(result.get('points'))
        current_points = parse_points(current.get('points'))
        position = _int(result.get('position')) or 10**6
        current_position = _int(current.get('position')) or 10**6
        if points > current_points or (
            points == current_points and position < current_position
        ):
            best[driver_id] = result
    return best


def _qualifying_entry(row):
    if not row:
        return None
    return {
        'position': _int(row.get('position')),
        'q1': row.get('Q1') or None,
        'q2': row.get('Q2') or None,
        'q3': row.get('Q3') or None,
    }


def _sprint_entry(row, season, round_number):
    if not row:
        return None
    labels = outcomes.classify(row, season, round_number)
    return {
        'finish': _int(row.get('position')),
        'positionText': str(row.get('positionText') or ''),
        'outcome': labels['outcome'],
        'points': format_points(parse_points(row.get('points'))),
    }


def build_rows():
    """Return ({driverId: [row, ...]}, coverage) from the stored race files."""
    rows_by_driver = defaultdict(list)
    coverage = {}
    for season in api_update.listed_seasons():
        results = api_update.load_json(f'races/{season}/results.json', [])
        qualifying = _by_round(f'races/{season}/qualifying.json')
        sprints = _by_round(f'races/{season}/sprint.json')
        details = _by_round(f'races/{season}/raceDetails.json')
        standings = api_update.load_json(f'races/{season}/driverStandings.json', {})
        constructor_positions = _final_positions(season, 'constructorStandings')
        season_coverage = {
            'races': len(results),
            'rows': 0,
            'indy500Races': 0,
            'hasQualifying': bool(qualifying),
            'hasSprints': bool(sprints),
            'hasDriverStandings': bool(standings),
            'hasConstructorStandings': bool(constructor_positions),
            'retired': 0,
            'retiredCauseKnown': 0,
            'unknownStatuses': set(),
        }
        for race in results:
            round_number = str(race.get('round'))
            indy = is_indy_500(race)
            season_coverage['indy500Races'] += int(indy)
            rows = race.get('Results') or []
            race_laps = max((_int(row.get('laps')) or 0 for row in rows), default=None)
            position_counts = defaultdict(int)
            for row in rows:
                position_counts[str(row.get('position'))] += 1
            pole = pole_sitter(
                season, race, qualifying.get(round_number),
                is_sprint_weekend(details.get(round_number)),
            ) or {}
            quali_rows = {
                (row.get('Driver') or {}).get('driverId'): row
                for row in (qualifying.get(round_number) or {}).get('QualifyingResults') or []
            }
            sprint_rows = {
                (row.get('Driver') or {}).get('driverId'): row
                for row in (sprints.get(round_number) or {}).get('SprintResults') or []
            }
            standing_rows = {
                (row.get('Driver') or {}).get('driverId'): row
                for row in standings.get(round_number) or []
            }
            best = _best_row_per_driver(rows)
            labelled = {}
            for driver_id, result in best.items():
                labels = outcomes.classify(result, season, round_number)
                grid = _int(result.get('grid'))
                pit_lane = None
                if grid == 0:
                    grid = None
                    pit_lane = True if int(season) >= PIT_LANE_GRID_FROM else None
                elif grid is not None:
                    pit_lane = False
                labelled[driver_id] = (result, labels, grid, pit_lane)
            by_constructor = defaultdict(list)
            for driver_id, (result, labels, grid, pit_lane) in labelled.items():
                constructor_id = (result.get('Constructor') or {}).get('constructorId')
                by_constructor[constructor_id].append(driver_id)
            for driver_id, (result, labels, grid, pit_lane) in labelled.items():
                constructor_id = (result.get('Constructor') or {}).get('constructorId')
                standing = standing_rows.get(driver_id)
                teammates = []
                for other_id in by_constructor[constructor_id]:
                    if other_id == driver_id:
                        continue
                    other, other_labels, other_grid, _ = labelled[other_id]
                    teammates.append({
                        'driverId': other_id,
                        'finish': _int(other.get('position')) if other_labels['classified'] else None,
                        'outcome': other_labels['outcome'],
                        'grid': other_grid,
                    })
                row = {
                    'season': int(season),
                    'round': int(round_number),
                    'raceName': race.get('raceName'),
                    'circuitId': (race.get('Circuit') or {}).get('circuitId'),
                    'date': race.get('date'),
                    'indy500': indy,
                    'constructorId': constructor_id,
                    'grid': grid,
                    'pitLaneStart': pit_lane,
                    'qualifying': _qualifying_entry(quali_rows.get(driver_id)),
                    'finish': _int(result.get('position')) if labels['classified'] else None,
                    'positionText': str(result.get('positionText') or ''),
                    'classified': labels['classified'],
                    'outcome': labels['outcome'],
                    'cause': labels['cause'],
                    'status': str(result.get('status') or ''),
                    'laps': _int(result.get('laps')),
                    'raceLaps': race_laps,
                    'points': format_points(parse_points(result.get('points'))),
                    'fastestLapRank': fastest_lap_rank(result),
                    'pole': pole.get('driverId') == driver_id,
                    'sharedDrive': position_counts.get(str(result.get('position')), 0) > 1,
                    'sprint': _sprint_entry(sprint_rows.get(driver_id), season, round_number),
                    'standingAfter': {
                        'position': _int(standing.get('position')),
                        'points': format_points(parse_points(standing.get('points'))),
                    } if standing else None,
                    'teammates': sorted(teammates, key=lambda item: item['driverId']),
                }
                rows_by_driver[driver_id].append(row)
                season_coverage['rows'] += 1
                if labels['dnf']:
                    season_coverage['retired'] += 1
                    season_coverage['retiredCauseKnown'] += int(labels['cause'] != 'unknown')
                if not labels['statusKnown']:
                    season_coverage['unknownStatuses'].add(row['status'])
        season_coverage['unknownStatuses'] = sorted(season_coverage['unknownStatuses'])
        season_coverage['constructorStandings'] = {
            constructor_id: entry['position']
            for constructor_id, entry in constructor_positions.items()
        }
        coverage[str(season)] = season_coverage
    for rows in rows_by_driver.values():
        rows.sort(key=lambda row: (row['season'], row['round']))
    return dict(rows_by_driver), coverage


def _blank_tally():
    return {
        'entries': 0,
        'starts': 0,
        'classifiedFinishes': 0,
        'wins': 0,
        'podiums': 0,
        'pointsFinishes': 0,
        'poles': 0,
        'fastestLaps': 0,
        'points': 0.0,
        'sprintPoints': 0.0,
        'dnfs': 0,
        'dnfsByCause': {cause: 0 for cause in outcomes.CAUSES},
        'dsq': 0,
        'dns': 0,
        'dnq': 0,
        'withdrew': 0,
        'indy500Entries': 0,
        'avgFinish': None,
        'avgGrid': None,
    }


def tally(rows):
    """As-recorded totals for a list of rows."""
    totals = _blank_tally()
    finishes = []
    grids = []
    for row in rows:
        totals['entries'] += 1
        totals['indy500Entries'] += int(row['indy500'])
        totals['points'] += float(row['points'])
        if row['sprint']:
            totals['sprintPoints'] += float(row['sprint']['points'])
        totals['poles'] += int(row['pole'])
        totals['fastestLaps'] += int(row['fastestLapRank'] == 1)
        outcome = row['outcome']
        if outcome in outcomes.STARTED_OUTCOMES:
            totals['starts'] += 1
        if outcome in ('dsq', 'dns', 'dnq', 'withdrew'):
            totals[outcome] += 1
        if outcome == 'retired':
            totals['dnfs'] += 1
            totals['dnfsByCause'][row['cause'] or 'unknown'] += 1
        if row['finish'] is not None:
            finishes.append(row['finish'])
            totals['classifiedFinishes'] += int(outcome in outcomes.CLASSIFIED_OUTCOMES)
            totals['wins'] += int(row['finish'] == 1)
            totals['podiums'] += int(row['finish'] <= 3)
        if float(row['points']) > 0:
            totals['pointsFinishes'] += 1
        if row['grid'] is not None:
            grids.append(row['grid'])
    totals['points'] = format_points(totals['points'])
    totals['sprintPoints'] = format_points(totals['sprintPoints'])
    if finishes:
        totals['avgFinish'] = round(sum(finishes) / len(finishes), 3)
    if grids:
        totals['avgGrid'] = round(sum(grids) / len(grids), 3)
    return totals


def build_summary(driver_id, rows, identity, coverage):
    seasons = {}
    for row in rows:
        seasons.setdefault(row['season'], []).append(row)
    season_blocks = {}
    for season, season_rows in sorted(seasons.items()):
        constructor_ids = []
        for row in season_rows:
            if row['constructorId'] and row['constructorId'] not in constructor_ids:
                constructor_ids.append(row['constructorId'])
        final = season_rows[-1]['standingAfter']
        standings = (coverage.get(str(season)) or {}).get('constructorStandings') or {}
        teammate_ids = sorted({
            teammate['driverId']
            for row in season_rows for teammate in row['teammates']
        })
        season_blocks[str(season)] = {
            **tally(season_rows),
            'constructorIds': constructor_ids,
            'constructorStandings': {
                constructor_id: standings.get(constructor_id)
                for constructor_id in constructor_ids
            },
            'standing': final,
            'teammateIds': teammate_ids,
        }
    return {
        'schemaVersion': SCHEMA_VERSION,
        'driverId': driver_id,
        'code': identity.get('code') or None,
        'permanentNumber': identity.get('permanentNumber') or None,
        'givenName': identity.get('givenName') or None,
        'familyName': identity.get('familyName') or None,
        'dateOfBirth': identity.get('dateOfBirth') or None,
        'nationality': identity.get('nationality') or None,
        'firstSeason': rows[0]['season'],
        'lastSeason': rows[-1]['season'],
        'career': tally(rows),
        'seasons': season_blocks,
    }


def validate_rows(driver_id, rows):
    """Return a list of schema problems; an empty list means the rows are valid."""
    problems = []
    for index, row in enumerate(rows):
        where = f'{driver_id}[{index}]'
        for key, expected in ROW_TYPES.items():
            if key not in row:
                problems.append(f'{where}: missing {key}')
            elif not isinstance(row[key], expected) or (
                expected is int and isinstance(row[key], bool)
            ):
                problems.append(f'{where}: {key} has {type(row[key]).__name__}')
        extra = set(row) - set(ROW_TYPES)
        if extra:
            problems.append(f'{where}: unexpected keys {sorted(extra)}')
        if row.get('outcome') not in outcomes.OUTCOMES:
            problems.append(f'{where}: bad outcome {row.get("outcome")!r}')
        cause = row.get('cause')
        if cause is not None and cause not in outcomes.CAUSES:
            problems.append(f'{where}: bad cause {cause!r}')
        if row.get('outcome') == 'retired' and cause is None:
            problems.append(f'{where}: retired without a cause')
        if row.get('finish') is not None and not row.get('classified'):
            problems.append(f'{where}: finish set on an unclassified row')
        if row.get('grid') == 0:
            problems.append(f'{where}: grid 0 should be null')
        if row.get('outcome') not in outcomes.STARTED_OUTCOMES and row.get('finish') is not None:
            problems.append(f'{where}: non-start with a finish')
    return problems


def _identity_map():
    return {
        row.get('driverId'): row
        for row in api_update.load_json('driversList.json', [])
        if row.get('driverId')
    }


def _write_if_changed(path, payload, text=None):
    """Write payload (or its preformatted text) unless the file already holds it."""
    if text is None:
        if api_update.load_json(path, None) == payload:
            return False
        api_update.write_json_atomic(path, payload)
        return True
    if os.path.exists(path):
        with open(path, encoding='utf-8', newline='') as handle:
            if handle.read() == text:
                return False
    api_update.write_text_atomic(path, text)
    return True


def _results_document(driver_id, rows):
    """Compact JSON with one row per line: a third of the size, still diffable."""
    lines = [
        json.dumps(row, ensure_ascii=False, separators=(',', ':'))
        for row in rows
    ]
    header = (
        f'{{"schemaVersion": {SCHEMA_VERSION}, '
        f'"driverId": {json.dumps(driver_id)}, "results": [\n'
    )
    return header + ',\n'.join(lines) + '\n]}\n'


def unknown_retirements(rows_by_driver):
    worklist = []
    for driver_id, rows in rows_by_driver.items():
        for row in rows:
            if row['outcome'] == 'retired' and row['cause'] == 'unknown':
                worklist.append({
                    'season': row['season'],
                    'round': row['round'],
                    'raceName': row['raceName'],
                    'driverId': driver_id,
                    'status': row['status'],
                    'laps': row['laps'],
                    'raceLaps': row['raceLaps'],
                    'classified': row['classified'],
                })
    worklist.sort(key=lambda item: (-item['season'], item['round'], item['driverId']))
    return worklist


def build_index(rows_by_driver, coverage):
    categories = api_update.load_json(outcomes.STATUS_FILE, {})
    seasons = sorted(int(season) for season in coverage)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'generatedAt': api_update.dt.now().isoformat(),
        'drivers': len(rows_by_driver),
        'firstSeason': seasons[0] if seasons else None,
        'lastSeason': seasons[-1] if seasons else None,
        'files': {
            'results': 'v2/drivers/{driverId}/results.json',
            'summary': 'v2/drivers/{driverId}/summary.json',
            'coverage': 'v2/coverage.json',
            'unknownRetirements': 'v2/unknownRetirements.json',
            'rescoredSeasons': 'scoring/rescored/{season}.json',
            'scoringSystems': 'scoringSystems.json',
        },
        'outcomes': categories.get('outcomes') or {},
        'causes': categories.get('causes') or {},
        'started': sorted(outcomes.STARTED_OUTCOMES),
        'notes': {
            'grid': 'null when Jolpica reports 0. pitLaneStart is true for such '
                    f'rows from {PIT_LANE_GRID_FROM}; before that it is null '
                    'because the grid slot was usually just not recorded.',
            'finish': 'null unless the row is classified; retired drivers keep '
                      'their outcome and cause instead of a position.',
            'teammates': 'Other drivers entered by the same constructor in the '
                         'same race, with their finish, outcome and grid.',
            'sharedDrive': 'True when another entry holds the same finishing '
                           'position (1950s shared cars).',
            'points': 'As awarded at the time. Use scoring/rescored for other '
                      'points systems.',
        },
    }


def publish_v2(write=True):
    """Build every v2 file. Raises if any row fails validation."""
    rows_by_driver, coverage = build_rows()
    identities = _identity_map()
    problems = []
    for driver_id, rows in rows_by_driver.items():
        problems.extend(validate_rows(driver_id, rows))
    if problems:
        preview = '\n'.join(problems[:20])
        raise RuntimeError(
            f'v2 validation found {len(problems)} problems:\n{preview}'
        )
    written = 0
    if write:
        for driver_id, rows in rows_by_driver.items():
            directory = os.path.join(V2_DIR, 'drivers', driver_id)
            written += _write_if_changed(
                os.path.join(directory, 'results.json'),
                {'schemaVersion': SCHEMA_VERSION, 'driverId': driver_id, 'results': rows},
                text=_results_document(driver_id, rows),
            )
            written += _write_if_changed(
                os.path.join(directory, 'summary.json'),
                build_summary(driver_id, rows, identities.get(driver_id, {}), coverage),
            )
        written += _write_if_changed(
            os.path.join(V2_DIR, 'coverage.json'),
            {'schemaVersion': SCHEMA_VERSION, 'seasons': coverage},
        )
        written += _write_if_changed(
            os.path.join(V2_DIR, 'unknownRetirements.json'),
            {
                'schemaVersion': SCHEMA_VERSION,
                'overridesFile': outcomes.RETIREMENT_OVERRIDES_FILE,
                'retirements': unknown_retirements(rows_by_driver),
            },
        )
        api_update.write_json_atomic(
            os.path.join(V2_DIR, 'index.json'), build_index(rows_by_driver, coverage)
        )
        written += 1
    unknown_count = sum(
        1 for rows in rows_by_driver.values()
        for row in rows if row['outcome'] == 'retired' and row['cause'] == 'unknown'
    )
    unknown_statuses = sorted({
        status
        for season in coverage.values()
        for status in season['unknownStatuses']
    })
    return {
        'drivers': len(rows_by_driver),
        'rows': sum(len(rows) for rows in rows_by_driver.values()),
        'written': written,
        'unknownRetirements': unknown_count,
        'unknownStatuses': unknown_statuses,
    }
