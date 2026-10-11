"""Rebuild driver JSON files from stored race data."""

from __future__ import annotations

import math
import os
from collections import defaultdict

import api_update
from outcomes import classify, did_not_finish, is_dnf  # noqa: F401 (re-exported)
from poles import is_sprint_weekend, pole_sitter, qualifying_p1
from scoring import is_classified, parse_points, fastest_lap_rank

BLANK_DRIVER = {
    'driverId': '',
    'driverCode': '',
    'driverNumber': '',
    'lastUpdate': '',
    'totalWins': 0,
    'totalPodiums': 0,
    'totalPoles': 0,
    'totalDNFs': 0,
    'seasonWins': {},
    'seasonPodiums': {},
    'seasonPoles': {},
    'seasonDNFs': {},
    'poles': {},
    'podiums': {},
    'DNFs': {},
    'fastLaps': {},
    'finalStandings': {},
    'posAfterRace': {},
    'racePosition': {},
    'qualiPosition': {},
    'driverQualifyingTimes': {},
    'consistency': {},
    'peakSeason': {},
    'avgRacePositions': {},
    'avgQualiPositions': {},
    'rates': {},
    'winRate': 0.0,
    'podiumRate': 0.0,
    'poleRate': 0.0,
    'dnfRate': 0.0,
    'ptwConRate': {},
    'positionsGainLost': {},
}


def _empty(value):
    return value in (None, '', [], {}, 0, 0.0, '0')


def _name(driver):
    return f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()


def _ensure_season(data, season):
    season = str(season)
    data.setdefault('seasonWins', {})
    data.setdefault('seasonPodiums', {})
    data.setdefault('seasonPoles', {})
    data.setdefault('seasonDNFs', {})
    data.setdefault('poles', {})
    data.setdefault('podiums', {})
    data.setdefault('DNFs', {})
    data.setdefault('fastLaps', {})
    data.setdefault('finalStandings', {})
    data.setdefault('posAfterRace', {})
    data.setdefault('racePosition', {})
    data.setdefault('qualiPosition', {})
    data.setdefault('driverQualifyingTimes', {})
    data.setdefault('raceResults', {})
    data.setdefault('sprintResults', {})
    data['seasonWins'].setdefault(season, 0)
    data['seasonPodiums'].setdefault(season, 0)
    data['seasonPoles'].setdefault(season, 0)
    data['seasonDNFs'].setdefault(season, 0)
    data['poles'].setdefault(season, [])
    data['podiums'].setdefault(season, {})
    data['DNFs'].setdefault(season, {})
    data['fastLaps'].setdefault(season, {})
    data['finalStandings'].setdefault(
        season, {'year': season, 'position': '0', 'points': '0'}
    )
    data['posAfterRace'].setdefault(season, {'year': season, 'pos': {}})
    data['racePosition'].setdefault(season, {'year': season, 'positions': {}})
    data['qualiPosition'].setdefault(season, {'year': season, 'positions': {}})
    data['driverQualifyingTimes'].setdefault(
        season, {'year': season, 'QualiTimes': {}}
    )
    data['raceResults'].setdefault(season, {})
    data['sprintResults'].setdefault(season, {})


def _blank_for(driver_id, driver=None):
    data = {key: (value.copy() if isinstance(value, dict) else value)
            for key, value in BLANK_DRIVER.items()}
    data['driverId'] = driver_id
    if driver:
        data['driverCode'] = driver.get('code', '')
        data['driverNumber'] = driver.get('permanentNumber', '')
    return data


def _position_counts(results):
    counts = defaultdict(int)
    for result in results:
        counts[str(result.get('position'))] += 1
    return counts


def collect_pole_diffs():
    """Races where grid-1 pole differs from qualifying P1."""
    diffs = []
    for season in api_update.listed_seasons():
        results = api_update.load_json(f'races/{season}/results.json', [])
        qualifying = {
            str(race.get('round')): race
            for race in api_update.load_json(f'races/{season}/qualifying.json', [])
        }
        details = {
            str(race.get('round')): race
            for race in api_update.load_json(f'races/{season}/raceDetails.json', [])
        }
        for race in results:
            round_number = str(race.get('round'))
            weekend = is_sprint_weekend(details.get(round_number))
            official = pole_sitter(
                season, race, qualifying.get(round_number), weekend
            )
            quali = qualifying_p1(qualifying.get(round_number))
            if not official or not quali:
                continue
            if official.get('driverId') != quali.get('driverId'):
                diffs.append({
                    'season': season,
                    'round': round_number,
                    'raceName': race.get('raceName'),
                    'poleDriverId': official.get('driverId'),
                    'poleName': official.get('name'),
                    'poleSource': official.get('source'),
                    'qualifyingP1': quali.get('driverId'),
                    'qualifyingName': quali.get('name'),
                    'sprintWeekend': weekend,
                })
    return diffs


def build_driver_maps():
    drivers = {}
    pole_by_race = []
    for season in api_update.listed_seasons():
        results = api_update.load_json(f'races/{season}/results.json', [])
        qualifying = {
            str(race.get('round')): race
            for race in api_update.load_json(f'races/{season}/qualifying.json', [])
        }
        sprints = {
            str(race.get('round')): race
            for race in api_update.load_json(f'races/{season}/sprint.json', [])
        }
        details = {
            str(race.get('round')): race
            for race in api_update.load_json(f'races/{season}/raceDetails.json', [])
        }
        standings = api_update.load_json(
            f'races/{season}/driverStandings.json', {}
        )
        season_key = str(season)
        for race in results:
            round_number = str(race.get('round'))
            race_name = race.get('raceName')
            rows = race.get('Results') or []
            counts = _position_counts(rows)
            weekend = is_sprint_weekend(details.get(round_number))
            official_pole = pole_sitter(
                season, race, qualifying.get(round_number), weekend
            )
            if official_pole and official_pole.get('driverId'):
                pole_by_race.append((season_key, race_name, official_pole['driverId']))
            finished_weekend = {}
            dnf_status = {}
            for result in rows:
                driver = result.get('Driver') or {}
                driver_id = driver.get('driverId')
                if not driver_id:
                    continue
                data = drivers.setdefault(driver_id, _blank_for(driver_id, driver))
                _ensure_season(data, season_key)
                classified = is_classified(result)
                position = result.get('position')
                previous = data['racePosition'][season_key]['positions'].get(race_name)
                previous_rank = int(previous) if str(previous).isdigit() else 10**6
                new_rank = int(position) if str(position).isdigit() else 10**6
                if previous is None or new_rank < previous_rank:
                    data['racePosition'][season_key]['positions'][race_name] = position
                status = result.get('status', '')
                labels = classify(result, season_key, round_number)
                if labels['dnf']:
                    dnf_status.setdefault(driver_id, status)
                else:
                    finished_weekend[driver_id] = True
                if position in ('1', '2', '3'):
                    current_podium = data['podiums'][season_key].get(race_name)
                    if current_podium is None or int(position) < int(current_podium):
                        data['podiums'][season_key][race_name] = position
                if result.get('FastestLap', {}).get('Time', {}).get('time'):
                    data['fastLaps'][season_key][race_name] = result['FastestLap']['Time']['time']
                elif race_name not in data['fastLaps'][season_key]:
                    data['fastLaps'][season_key][race_name] = -1
                if result.get('grid') is not None:
                    if race_name not in data['qualiPosition'][season_key]['positions'] or new_rank <= previous_rank:
                        data['qualiPosition'][season_key]['positions'][race_name] = result.get('grid')
                existing_round = data['raceResults'][season_key].get(round_number)
                payload = {
                    'raceName': race_name,
                    'position': int(position) if str(position).isdigit() else None,
                    'positionText': result.get('positionText'),
                    'classified': classified,
                    'grid': int(result['grid']) if str(result.get('grid', '')).isdigit() else None,
                    'points': parse_points(result.get('points')),
                    'status': status,
                    'outcome': labels['outcome'],
                    'cause': labels['cause'],
                    'constructorId': (result.get('Constructor') or {}).get('constructorId'),
                    'fastestLapRank': fastest_lap_rank(result),
                    'shared': counts.get(str(position), 0) > 1,
                }
                if existing_round is None or (
                    (payload['points'] or 0) > (existing_round.get('points') or 0)
                    or (
                        payload['position'] is not None
                        and (
                            existing_round.get('position') is None
                            or payload['position'] < existing_round['position']
                        )
                    )
                ):
                    data['raceResults'][season_key][round_number] = payload
            for driver_id, status in dnf_status.items():
                data = drivers.get(driver_id)
                if not data:
                    continue
                if finished_weekend.get(driver_id):
                    data['DNFs'][season_key].pop(race_name, None)
                else:
                    data['DNFs'][season_key][race_name] = status
            sprint_race = sprints.get(round_number)
            if sprint_race:
                for result in sprint_race.get('SprintResults') or []:
                    driver = result.get('Driver') or {}
                    driver_id = driver.get('driverId')
                    if not driver_id:
                        continue
                    data = drivers.setdefault(driver_id, _blank_for(driver_id, driver))
                    _ensure_season(data, season_key)
                    data['sprintResults'][season_key][round_number] = {
                        'position': int(result['position']) if str(result.get('position', '')).isdigit() else None,
                        'positionText': result.get('positionText'),
                        'classified': is_classified(result),
                        'points': parse_points(result.get('points')),
                        'constructorId': (result.get('Constructor') or {}).get('constructorId'),
                    }
            for row in standings.get(round_number) or []:
                driver_id = (row.get('Driver') or {}).get('driverId')
                if not driver_id:
                    continue
                data = drivers.setdefault(
                    driver_id, _blank_for(driver_id, row.get('Driver'))
                )
                _ensure_season(data, season_key)
                points = parse_points(row.get('points'))
                data['posAfterRace'][season_key]['pos'][race_name] = {'points': points}
                data['finalStandings'][season_key] = {
                    'year': season_key,
                    'position': str(row.get('position', '40')),
                    'points': row.get('points'),
                }
            quali_race = qualifying.get(round_number)
            if quali_race:
                for row in quali_race.get('QualifyingResults') or []:
                    driver_id = (row.get('Driver') or {}).get('driverId')
                    if not driver_id:
                        continue
                    data = drivers.setdefault(
                        driver_id, _blank_for(driver_id, row.get('Driver'))
                    )
                    _ensure_season(data, season_key)
                    data['driverQualifyingTimes'][season_key]['QualiTimes'][race_name] = [
                        row.get('Q1', 'N/A'),
                        row.get('Q2', 'N/A'),
                        row.get('Q3', 'N/A'),
                    ]
        for season_key, race_name, driver_id in [
            item for item in pole_by_race if item[0] == str(season)
        ]:
            data = drivers.get(driver_id)
            if not data:
                continue
            _ensure_season(data, season_key)
            if race_name not in data['poles'][season_key]:
                data['poles'][season_key].append(race_name)

    for data in drivers.values():
        for season, names in data.get('poles', {}).items():
            data['seasonPoles'][season] = len(names)
        for season, positions in data.get('racePosition', {}).items():
            values = list((positions.get('positions') or {}).values())
            data['seasonWins'][season] = values.count('1')
            data['seasonPodiums'][season] = len(data.get('podiums', {}).get(season, {}))
            data['seasonDNFs'][season] = len(data.get('DNFs', {}).get(season, {}))
        data['totalWins'] = sum(data.get('seasonWins', {}).values())
        data['totalPodiums'] = sum(data.get('seasonPodiums', {}).values())
        data['totalPoles'] = sum(data.get('seasonPoles', {}).values())
        data['totalDNFs'] = sum(data.get('seasonDNFs', {}).values())
    return drivers


def _mean(values):
    return sum(values) / len(values) if values else None


def _consistency(values):
    mean = _mean(values) or 0.0
    std = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) if values else 0.0
    return mean, std, (std / mean if mean else 0)


def _peak(values, seasons):
    if not values or max(values) == 0:
        return 'No peak season', 0
    index = values.index(max(values))
    return seasons[index], values[index]


def _season_labels(data, season):
    """raceName -> outcome labels for every stored round of a season."""
    labels = {}
    for payload in ((data.get('raceResults') or {}).get(season) or {}).values():
        labels[payload.get('raceName')] = classify({
            'positionText': payload.get('positionText'),
            'status': payload.get('status'),
        })
    return labels


def analyse_driver(data):
    """Derive the summary fields (rates, averages, consistency) in place.

    Rates divide by starts: DNQs, withdrawals and non-starts are not races.
    Grid 0 (pit lane, or simply unrecorded) stays out of qualifying averages
    and positions gained. A season with no usable values reports -1, as the
    old NaN replacement did.
    """
    seasons = sorted(
        set(data.get('seasonWins') or {}) | set(data.get('racePosition') or {})
    )
    wins = [int((data.get('seasonWins') or {}).get(season, 0)) for season in seasons]
    podiums = [int((data.get('seasonPodiums') or {}).get(season, 0)) for season in seasons]
    poles = [int((data.get('seasonPoles') or {}).get(season, 0)) for season in seasons]
    dnfs = [int((data.get('seasonDNFs') or {}).get(season, 0)) for season in seasons]
    points = [
        parse_points(((data.get('finalStandings') or {}).get(season) or {}).get('points'))
        for season in seasons
    ]

    starts = {}
    avg_race = {}
    avg_quali = {}
    gained = {}
    pole_conversion = {}
    for season in seasons:
        race_positions = ((data.get('racePosition') or {}).get(season) or {}).get('positions') or {}
        quali_positions = ((data.get('qualiPosition') or {}).get(season) or {}).get('positions') or {}
        labels = _season_labels(data, season)
        if labels:
            started = {name for name, label in labels.items() if label['started']}
        else:
            started = set(race_positions)
        starts[season] = len(started)
        race_values = [
            int(position) for name, position in race_positions.items()
            if name in started and str(position).isdigit()
        ]
        quali_values = [
            int(grid) for name, grid in quali_positions.items()
            if name in started and str(grid).isdigit() and int(grid) > 0
        ]
        avg_race[season] = _mean(race_values) if race_values else -1
        avg_quali[season] = _mean(quali_values) if quali_values else -1
        gained[season] = {
            name: int(quali_positions[name]) - int(position)
            for name, position in race_positions.items()
            if name in started
            and str(position).isdigit()
            and str(quali_positions.get(name)).isdigit()
            and int(quali_positions[name]) > 0
        }
        pole_races = (data.get('poles') or {}).get(season) or []
        if pole_races:
            converted = sum(
                1 for name in pole_races if str(race_positions.get(name)) == '1'
            )
            pole_conversion[season] = converted / len(pole_races)
        else:
            pole_conversion[season] = -1

    def rate(count, season):
        return count / starts[season] if starts[season] else 0.0

    total_starts = sum(starts.values())
    mean_wins, std_wins, cv_wins = _consistency(wins)
    mean_podiums, std_podiums, cv_podiums = _consistency(podiums)
    mean_poles, std_poles, cv_poles = _consistency(poles)
    mean_points, std_points, cv_points = _consistency(points)
    peak_wins = _peak(wins, seasons)
    peak_podiums = _peak(podiums, seasons)
    peak_poles = _peak(poles, seasons)

    data['consistency'] = {
        'mean': {'wins': mean_wins, 'podiums': mean_podiums, 'poles': mean_poles, 'points': mean_points},
        'std': {'wins': std_wins, 'podiums': std_podiums, 'poles': std_poles, 'points': std_points},
        'cv': {'wins': cv_wins, 'podiums': cv_podiums, 'poles': cv_poles, 'points': cv_points},
    }
    data['peakSeason'] = {
        'wins': {'season': peak_wins[0], 'wins': peak_wins[1]},
        'podiums': {'season': peak_podiums[0], 'podiums': peak_podiums[1]},
        'poles': {'season': peak_poles[0], 'poles': peak_poles[1]},
    }
    data['avgRacePositions'] = avg_race
    data['avgQualiPositions'] = avg_quali
    data['rates'] = {
        'wins': {season: rate(wins[index], season) for index, season in enumerate(seasons)},
        'podiums': {season: rate(podiums[index], season) for index, season in enumerate(seasons)},
        'poles': {season: rate(poles[index], season) for index, season in enumerate(seasons)},
        'DNFs': {season: rate(dnfs[index], season) for index, season in enumerate(seasons)},
    }
    data['seasonStarts'] = starts
    data['totalStarts'] = total_starts
    data['winRate'] = data.get('totalWins', 0) / total_starts if total_starts else 0.0
    data['podiumRate'] = data.get('totalPodiums', 0) / total_starts if total_starts else 0.0
    data['poleRate'] = data.get('totalPoles', 0) / total_starts if total_starts else 0.0
    data['dnfRate'] = data.get('totalDNFs', 0) / total_starts if total_starts else 0.0
    data['ptwConRate'] = pole_conversion
    data['positionsGainLost'] = gained
    return data


def _list_len(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return None


def shrinking_changes(old, new, pole_diff_races):
    """Return explained and unexplained shrinking lists/counts."""
    explained = []
    unexplained = []
    pole_races = {
        (str(item['season']), item['raceName']) for item in pole_diff_races
    }

    def consider(path, old_value, new_value):
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            keys = set(old_value) | set(new_value)
            for key in keys:
                consider(f'{path}.{key}', old_value.get(key), new_value.get(key))
            return
        old_count = old_value if isinstance(old_value, (int, float)) else _list_len(old_value)
        new_count = new_value if isinstance(new_value, (int, float)) else _list_len(new_value)
        if old_count is None or new_count is None:
            if isinstance(old_value, list) and isinstance(new_value, list):
                removed = [item for item in old_value if item not in new_value]
                if not removed:
                    return
                season = path.split('.')[1] if '.' in path else None
                if all((str(season), name) in pole_races for name in removed):
                    explained.append({'path': path, 'removed': removed})
                else:
                    unexplained.append({
                        'path': path, 'old': old_value, 'new': new_value,
                    })
            return
        if new_count < old_count:
            record = {'path': path, 'old': old_count, 'new': new_count}
            if any(token in path.lower() for token in ('pole', 'win', 'dnf', 'podium')):
                explained.append(record)
            else:
                unexplained.append(record)

    for key in (
        'totalWins', 'totalPodiums', 'totalPoles', 'totalDNFs',
        'seasonWins', 'seasonPodiums', 'seasonPoles', 'seasonDNFs',
        'poles', 'podiums', 'DNFs',
    ):
        consider(key, old.get(key), new.get(key))
    return explained, unexplained


def _without_timestamp(data):
    return {key: value for key, value in data.items() if key != 'lastUpdate'}


def rebuild_driver_files(write=True):
    """Rebuild every driver file from the stored race files.

    This is the daily path: the race files are the source of truth, so each
    run recomputes everything and only rewrites files whose content changed.
    Nothing is written when a count shrinks for no known reason.
    """
    pole_diffs = collect_pole_diffs()
    rebuilt = build_driver_maps()
    explained_all = []
    unexplained_all = []
    written = 0
    unchanged = 0
    existing_files = {
        name[:-5]
        for name in os.listdir('drivers')
        if name.endswith('.json')
    }
    pending = []
    for driver_id, payload in rebuilt.items():
        path = f'drivers/{driver_id}.json'
        analyse_driver(payload)
        existing = api_update.load_json(path, None)
        if existing:
            merged = {**existing, **payload}
            # Jolpica's Driver object only carries a code and number for
            # modern drivers; keep the identity fields we already had.
            for key in ('driverCode', 'driverNumber'):
                if _empty(payload.get(key)) and not _empty(existing.get(key)):
                    merged[key] = existing[key]
            explained, unexplained = shrinking_changes(
                existing, merged, pole_diffs
            )
            if unexplained:
                unexplained_all.append({
                    'driverId': driver_id,
                    'changes': unexplained,
                })
                continue
            if explained:
                explained_all.append({
                    'driverId': driver_id,
                    'changes': explained,
                })
            payload = merged
            if _without_timestamp(payload) == _without_timestamp(existing):
                unchanged += 1
                continue
        pending.append((path, payload))
    if write and not unexplained_all:
        stamp = api_update.dt.now().isoformat()
        for path, payload in pending:
            payload['lastUpdate'] = stamp
            api_update.write_json_atomic(path, payload)
            written += 1
    list_rows = rebuild_drivers_list(
        write=write and not unexplained_all, keep_ids=set(rebuilt)
    )
    list_ids = {row.get('driverId') for row in list_rows}
    return {
        'written': written,
        'unchanged': unchanged,
        'explained': explained_all,
        'unexplained': unexplained_all,
        'poleDiffs': pole_diffs,
        'driverFileCount': len(existing_files),
        'driversListCount': len(list_ids),
        'filesNotInList': sorted(existing_files - list_ids),
        'listNotInFiles': sorted(list_ids - existing_files),
        'rebuiltCount': len(rebuilt),
        # Files no race, sprint, qualifying or standings row produces. They
        # are stale leftovers and are never rewritten.
        'orphanFiles': sorted(existing_files - set(rebuilt)),
    }


LIST_FIELDS = (
    'driverId', 'url', 'givenName', 'familyName', 'dateOfBirth', 'nationality',
)


def rebuild_drivers_list(write=True, keep_ids=None):
    """Rebuild driversList.json from existing entries plus Driver objects in race files.

    With keep_ids (the drivers the rebuild produced), existing entries for
    anyone else are dropped so the list never points at a driver file that
    no longer exists.
    """
    existing = api_update.load_json('driversList.json', [])
    by_id = {}
    for row in existing:
        driver_id = row.get('driverId')
        if driver_id and (keep_ids is None or driver_id in keep_ids):
            by_id[driver_id] = dict(row)
    for season in api_update.listed_seasons():
        races = api_update.load_json(f'races/{season}/results.json', [])
        sprints = api_update.load_json(f'races/{season}/sprint.json', [])
        for race in list(races) + list(sprints):
            rows = race.get('Results') or race.get('SprintResults') or []
            for result in rows:
                driver = result.get('Driver') or {}
                driver_id = driver.get('driverId')
                if not driver_id:
                    continue
                entry = by_id.setdefault(driver_id, {'driverId': driver_id})
                for field in LIST_FIELDS:
                    value = driver.get(field)
                    if value and not entry.get(field):
                        entry[field] = value
    rows = [by_id[key] for key in sorted(by_id)]
    if write:
        api_update.write_json_atomic('driversList.json', rows)
    return rows


def refresh_season_poles(season, directory='drivers'):
    """Rewrite one season's poles from pole_sitter so daily files match teamRecords."""

    season_key = str(season)
    results = api_update.load_json(f'races/{season}/results.json', [])
    qualifying = {
        str(race.get('round')): race
        for race in api_update.load_json(f'races/{season}/qualifying.json', [])
    }
    details = {
        str(race.get('round')): race
        for race in api_update.load_json(f'races/{season}/raceDetails.json', [])
    }
    poles_by_driver = defaultdict(list)
    for race in results:
        official = pole_sitter(
            season,
            race,
            qualifying.get(str(race.get('round'))),
            is_sprint_weekend(details.get(str(race.get('round')))),
        )
        driver_id = (official or {}).get('driverId')
        if driver_id:
            poles_by_driver[driver_id].append(race.get('raceName'))

    updated = 0
    if not os.path.isdir(directory):
        return updated
    for name in os.listdir(directory):
        if not name.endswith('.json'):
            continue
        path = os.path.join(directory, name)
        data = api_update.load_json(path, None)
        if not isinstance(data, dict):
            continue
        driver_id = data.get('driverId') or name[:-5]
        names = list(poles_by_driver.get(driver_id, []))
        current = list(data.get('poles', {}).get(season_key) or [])
        if names == current:
            continue
        data.setdefault('poles', {})
        data.setdefault('seasonPoles', {})
        data['poles'][season_key] = names
        data['seasonPoles'][season_key] = len(names)
        data['totalPoles'] = sum(data.get('seasonPoles', {}).values())
        api_update.write_json_atomic(path, data)
        updated += 1
    return updated
