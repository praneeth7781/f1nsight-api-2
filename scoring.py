"""Scoring overrides, validation, and hypothetical rescoring."""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict

import api_update

OVERRIDES_FILE = 'scoringOverrides.json'
SYSTEMS_FILE = 'scoringSystems.json'
RESCORED_DIR = 'scoring/rescored'
SCORING_INDEX_FILE = 'scoring/index.json'
POINTS_TOLERANCE = 0.02


def parse_points(value):
    """Keep fractional championship points. Never truncate with int()."""
    if value in (None, '', '-'):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Cannot parse points value {value!r}') from exc


def format_points(value):
    number = float(value)
    if abs(number - round(number)) < 1e-12:
        return int(round(number))
    return number


def _looks_like_jolpica_points(value):
    return abs(value - round(value, 2)) < 1e-9


def load_overrides():
    payload = api_update.load_json(OVERRIDES_FILE, {'overrides': []})
    return payload.get('overrides') or []


def load_systems_file():
    if not os.path.exists(SYSTEMS_FILE):
        raise RuntimeError(f'Missing {SYSTEMS_FILE}')
    with open(SYSTEMS_FILE, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def systems_list(data=None):
    data = data or load_systems_file()
    return data.get('systems') or []


def system_for_year(year, data=None):
    year = int(year)
    for system in systems_list(data):
        start = int(system['yearFrom'])
        end = system.get('yearTo')
        if year >= start and (end is None or year <= int(end)):
            return system
    raise RuntimeError(f'No scoring system covers {year}')


def drop_rule_for_year(year, kind='drivers', data=None):
    data = data or load_systems_file()
    year = int(year)
    if kind == 'constructors' and year < 1958:
        return None
    drivers = (data.get('dropRules') or {}).get('drivers') or {}
    rule = drivers.get(str(year))
    if kind == 'constructors':
        constructors = (data.get('dropRules') or {}).get('constructors') or {}
        if year > int(constructors.get('bestCarOnlyThrough', 1978)):
            return {'type': 'none'}
        return rule or {'type': 'none'}
    return rule or {'type': 'none'}


def is_classified(result):
    text = str(result.get('positionText', ''))
    return text.isdigit()


def is_indy_500(race):
    return 'indianapolis' in str(race.get('raceName', '')).lower()


def _driver_id(entry):
    driver = entry.get('Driver') or {}
    return driver.get('driverId')


def _constructor_id(entry):
    constructor = entry.get('Constructor') or {}
    if constructor.get('constructorId'):
        return constructor['constructorId']
    constructors = entry.get('Constructors') or []
    if constructors:
        return constructors[0].get('constructorId')
    return None


def _driver_name(entry):
    driver = entry.get('Driver') or {}
    name = f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()
    return name or _driver_id(entry) or ''


def _apply_add_once(current, add):
    value = parse_points(current)
    add = parse_points(add)
    without = value - add
    if _looks_like_jolpica_points(without) and not _looks_like_jolpica_points(value):
        return value
    if _looks_like_jolpica_points(value):
        return value + add
    return value


def result_matches_override(result, override):
    if _driver_id(result) != override.get('driverId'):
        return False
    number = override.get('number')
    if number is None:
        return True
    return str(result.get('number')) == str(number)


def _snap_fastest_lap_seventh(current):
    """Force the 1954 British GP 1/7 share, undoing 0.14 rounding or a double add."""
    value = parse_points(current)
    whole = math.floor(value + 1e-9)
    frac = value - whole
    seventh = 1.0 / 7.0
    if (
        abs(frac) < 1e-9
        or abs(frac - 0.14) < 0.005
        or abs(frac - seventh) < 1e-6
        or abs(frac - 2 * seventh) < 0.01
        or abs(frac - 0.14 - seventh) < 0.01
        or abs(frac - 0.002857142857142891) < 0.001
    ):
        return whole + seventh
    return value


def apply_scoring_overrides(seasons=None):
    """Apply documented corrections. Idempotent so a re-download cannot undo them."""
    overrides = load_overrides()
    if not overrides:
        return 0
    targets = seasons or api_update.listed_seasons()
    changed = 0
    by_season = defaultdict(list)
    for override in overrides:
        by_season[str(override.get('season'))].append(override)

    for season in targets:
        season_key = str(season)
        season_overrides = by_season.get(season_key, [])
        if not season_overrides:
            continue
        result_overrides = [
            item for item in season_overrides if item.get('target') == 'results'
        ]
        standing_overrides = [
            item for item in season_overrides
            if item.get('target') == 'driverStandings'
        ]
        if result_overrides:
            path = f'races/{season}/results.json'
            races = api_update.load_json(path, [])
            file_changed = False
            for race in races:
                if str(race.get('season')) != season_key:
                    continue
                for result in race.get('Results') or []:
                    for override in result_overrides:
                        if str(race.get('round')) != str(override.get('round')):
                            continue
                        if not result_matches_override(result, override):
                            continue
                        field = override.get('field', 'points')
                        new_value = override['value']
                        if parse_points(result.get(field)) != parse_points(new_value):
                            result[field] = format_points(new_value)
                            file_changed = True
            if file_changed:
                api_update.write_json_atomic(path, races)
                changed += 1
                print(f'Applied result overrides for {season}')

        if standing_overrides:
            path = f'races/{season}/driverStandings.json'
            standings = api_update.load_json(path, {})
            if not isinstance(standings, dict):
                continue
            file_changed = False
            for round_key, rows in standings.items():
                if not str(round_key).isdigit() or not isinstance(rows, list):
                    continue
                round_number = int(round_key)
                for row in rows:
                    for override in standing_overrides:
                        if _driver_id(row) != override.get('driverId'):
                            continue
                        round_from = int(override.get('roundFrom', override.get('round', 1)))
                        if round_number < round_from:
                            continue
                        field = override.get('field', 'points')
                        if override.get('snapSeventh'):
                            new_value = _snap_fastest_lap_seventh(row.get(field))
                        elif 'value' in override:
                            new_value = parse_points(override['value'])
                        else:
                            new_value = _apply_add_once(row.get(field), override['add'])
                        if abs(parse_points(row.get(field)) - new_value) > 1e-12:
                            row[field] = str(format_points(new_value))
                            file_changed = True
            if file_changed:
                numeric = [key for key in standings if str(key).isdigit()]
                if numeric:
                    latest = max(numeric, key=int)
                    standings['latest'] = standings[latest]
                api_update.write_json_atomic(path, standings)
                changed += 1
                print(f'Applied standings overrides for {season}')
    return changed


def race_multiplier(season, round_number, data=None):
    data = data or load_systems_file()
    shortened = data.get('shortenedRaces') or {}
    season = int(season)
    round_number = str(round_number)
    for race in shortened.get('halfPoints') or []:
        if int(race['season']) == season and str(race['round']) == round_number:
            return 0.5
    for race in shortened.get('doublePoints') or []:
        if int(race['season']) == season and str(race['round']) == round_number:
            return 2.0
    return 1.0


def share_factor(season, result, results, data=None):
    data = data or load_systems_file()
    shared = data.get('sharedCars') or {}
    split_through = int(shared.get('splitThrough', 1957))
    zero_from = int(shared.get('zeroFrom', 1958))
    position = str(result.get('position'))
    peers = [
        row for row in results
        if str(row.get('position')) == position
    ]
    count = max(len(peers), 1)
    if count == 1:
        return 1.0, False
    season = int(season)
    if season <= split_through:
        return 1.0 / count, True
    if season >= zero_from:
        return 0.0, True
    return 1.0, True


def position_points(points_table, classified_position):
    if classified_position is None:
        return 0.0
    index = classified_position - 1
    if index < 0 or index >= len(points_table):
        return 0.0
    return float(points_table[index])


def classified_position(result):
    if not is_classified(result):
        return None
    return int(result['positionText'])


def fastest_lap_rank(result):
    rank = (result.get('FastestLap') or {}).get('rank')
    if rank in (None, '', '-'):
        return None
    try:
        return int(rank)
    except (TypeError, ValueError):
        return None


def recover_fastest_lap_shares(season, race, era_system, data=None):
    """Leftover after position points is the fastest-lap bonus.

    Cap leftover at the FL point value so a shared-car allocation mismatch
    (e.g. 1957 British GP P4, 3 vs 1.5+1.5) is not treated as extra FL.
    """
    season = int(season)
    results = race.get('Results') or []
    multiplier = race_multiplier(season, race.get('round'), data)
    fl_cap = float((era_system.get('fastestLap') or {}).get('points') or 0)
    if fl_cap <= 0:
        return {}
    shares = defaultdict(float)
    for result in results:
        driver_id = _driver_id(result)
        if not driver_id:
            continue
        share, _shared = share_factor(season, result, results, data)
        classified = classified_position(result)
        era_points = position_points(era_system.get('racePoints') or [], classified)
        expected = era_points * multiplier * share
        leftover = parse_points(result.get('points')) - expected
        if 0.05 < leftover <= fl_cap * multiplier + 0.05:
            shares[driver_id] += leftover
    total = sum(shares.values())
    if total <= 0.05:
        return {}
    return {driver_id: value / total for driver_id, value in shares.items() if value > 0.05}


def fastest_lap_recipients(season, race, era_system, data=None):
    season = int(season)
    data = data or load_systems_file()
    gap = ((data.get('gaps') or {}).get('fastestLapUnavailable') or {})
    if int(gap.get('from', 1960)) <= season <= int(gap.get('to', 2003)):
        return None
    ranked = []
    seen = set()
    for result in race.get('Results') or []:
        if fastest_lap_rank(result) != 1:
            continue
        driver_id = _driver_id(result)
        if not driver_id or driver_id in seen:
            continue
        seen.add(driver_id)
        ranked.append(driver_id)
    if ranked:
        return {driver_id: 1.0 / len(ranked) for driver_id in ranked}
    if season <= 1959 or season >= 2019:
        return recover_fastest_lap_shares(season, race, era_system, data)
    return {}


def score_entries(
    season, race, target_system, era_system, data=None, kind='drivers',
    awarded=False,
):
    """Score one Grand Prix under target_system using share + race multiplier.

    Own-era scoring (`awarded=True`) uses the points already stored in
    races/{season}/results.json, not the collapsed driver-file raceResults.
    Fastest-lap bonus is applied once per driver, even if they drove two cars.
    """
    data = data or load_systems_file()
    season = int(season)
    results = race.get('Results') or []
    multiplier = race_multiplier(season, race.get('round'), data)
    fl_rule = target_system.get('fastestLap') or {}
    era_fl_rule = era_system.get('fastestLap') or {}
    fl_recipients = fastest_lap_recipients(season, race, era_system, data)
    scored = []
    fl_given = set()
    for result in results:
        share, shared = share_factor(season, result, results, data)
        classified = classified_position(result)
        driver_id = _driver_id(result)
        fl_points = 0.0
        if awarded:
            points = parse_points(result.get('points'))
            fl_share = (fl_recipients or {}).get(driver_id, 0.0)
            era_fl = float(era_fl_rule.get('points') or 0)
            if (
                season <= 1959 and era_fl and fl_share
                and driver_id not in fl_given
            ):
                fl_points = min(points, era_fl * fl_share)
                if fl_points:
                    fl_given.add(driver_id)
            if kind == 'constructors' and season <= 1959:
                points -= fl_points
                fl_points = 0.0
        else:
            points = position_points(
                target_system.get('racePoints') or [], classified
            ) * multiplier * share
            fl_share = 0.0
            if fl_recipients is not None and fl_rule.get('points'):
                fl_share = fl_recipients.get(driver_id, 0.0)
                award = True
                if kind == 'constructors' and not fl_rule.get('constructors'):
                    award = False
                if season <= 1959 and kind == 'constructors':
                    award = False
                if fl_rule.get('top10Only') and (
                    classified is None or classified > 10
                ):
                    award = False
                if award and fl_share and driver_id not in fl_given:
                    fl_points = float(fl_rule['points']) * fl_share
                    points += fl_points
                    fl_given.add(driver_id)
        scored.append({
            'driverId': driver_id,
            'name': _driver_name(result),
            'constructorId': _constructor_id(result),
            'points': points,
            'flPoints': fl_points,
            'classifiedPosition': classified,
            'shared': shared,
            'status': result.get('status'),
        })
    return scored, fl_recipients is None


def score_sprint(season, race, target_system, awarded=False):
    table = target_system.get('sprintPoints') or []
    if not awarded and not table:
        return []
    scored = []
    for result in race.get('SprintResults') or []:
        classified = classified_position(result)
        if awarded:
            points = parse_points(result.get('points'))
        else:
            points = position_points(table, classified)
        scored.append({
            'driverId': _driver_id(result),
            'name': _driver_name(result),
            'constructorId': _constructor_id(result),
            'points': points,
            'classifiedPosition': classified,
        })
    return scored


def apply_drop_rules(results, rule, rounds=None):
    """Drop worst scores. Split seasons slice by calendar round, not list index.

    `results` is one numeric value per car/result. When `rounds` is given,
    a missed race does not pull a later result into the first half. Extra
    cars in one race all stay in that round's half.
    """
    if not results:
        return 0.0, 0.0
    gross = sum(results)
    if not rule or rule.get('type') == 'none':
        return gross, gross
    if rule.get('type') == 'bestN':
        counted = sorted(results, reverse=True)[: int(rule['n'])]
        return gross, sum(counted)
    if rule.get('type') == 'split':
        counted = []
        if rounds is None:
            index = 0
            for half in rule.get('halves') or []:
                length = int(half['races'])
                chunk = results[index:index + length]
                index += length
                counted.extend(sorted(chunk, reverse=True)[: int(half['best'])])
        else:
            start = 1
            paired = list(zip(results, rounds))
            for half in rule.get('halves') or []:
                length = int(half['races'])
                half_rounds = set(range(start, start + length))
                chunk = [
                    points for points, round_number in paired
                    if int(round_number) in half_rounds
                ]
                counted.extend(sorted(chunk, reverse=True)[: int(half['best'])])
                start += length
        return gross, sum(counted)
    raise RuntimeError(f'Unknown drop rule {rule}')


def countback_tuple(position_counts):
    counts = []
    max_pos = max(position_counts, default=0)
    for place in range(1, max(max_pos, 20) + 1):
        counts.append(-int(position_counts.get(place, 0)))
    return tuple(counts)


def official_standings_map(season, kind):
    rows = api_update._final_standings(season, kind)
    mapping = {}
    for row in rows:
        if kind == 'driverStandings':
            key = _driver_id(row)
        else:
            key = _constructor_id(row)
        if not key:
            continue
        mapping[key] = {
            'points': parse_points(row.get('points')),
            'position': int(row.get('position') or 0),
            'name': _driver_name(row) if kind == 'driverStandings' else (
                (row.get('Constructor') or {}).get('name') or key
            ),
        }
    return mapping


def rescore_season(season, system, drop_mode, data=None):
    """Rescore from races/{season}/results.json (and sprint.json), never driver files."""
    data = data or load_systems_file()
    season = int(season)
    era_system = system_for_year(season, data)
    own_era = system.get('id') == era_system.get('id')
    peel_fl = own_era and drop_mode == 'season' and season <= 1959
    races = api_update.load_json(f'races/{season}/results.json', [])
    sprints = api_update.load_json(f'races/{season}/sprint.json', [])
    sprints_by_round = {str(race.get('round')): race for race in sprints}
    driver_results = defaultdict(list)
    driver_rounds = defaultdict(list)
    driver_fl = defaultdict(float)
    driver_sprint = defaultdict(list)
    driver_meta = {}
    driver_places = defaultdict(lambda: defaultdict(int))
    constructor_results = defaultdict(list)
    constructor_rounds = defaultdict(list)
    constructor_places = defaultdict(lambda: defaultdict(int))
    constructor_meta = {}
    unavailable_fl = False

    constructors_cfg = (data.get('dropRules') or {}).get('constructors') or {}
    best_car_only = season <= int(constructors_cfg.get('bestCarOnlyThrough', 1978))

    for race in sorted(races, key=lambda item: int(item.get('round', 0))):
        round_number = int(race.get('round', 0))
        scored, missing_fl = score_entries(
            season, race, system, era_system, data,
            kind='drivers', awarded=own_era,
        )
        unavailable_fl = unavailable_fl or missing_fl
        for entry in scored:
            driver_id = entry['driverId']
            if not driver_id:
                continue
            fl_points = (
                peel_fastest_lap_points(entry.get('flPoints', 0.0))
                if peel_fl else 0.0
            )
            driver_results[driver_id].append(entry['points'] - fl_points)
            driver_rounds[driver_id].append(round_number)
            driver_fl[driver_id] += fl_points
            driver_meta[driver_id] = entry['name']
            if entry['classifiedPosition']:
                driver_places[driver_id][entry['classifiedPosition']] += 1
        sprint = score_sprint(
            season, sprints_by_round.get(str(race.get('round')), {}),
            system, awarded=own_era,
        )
        for entry in sprint:
            driver_id = entry['driverId']
            if not driver_id:
                continue
            driver_sprint[driver_id].append(entry['points'])
            driver_meta.setdefault(driver_id, entry['name'])

        if season >= 1958 and not is_indy_500(race):
            constructor_scored, _ = score_entries(
                season, race, system, era_system, data,
                kind='constructors', awarded=own_era,
            )
            sprint_by_constructor = defaultdict(float)
            for entry in sprint:
                if entry['constructorId']:
                    sprint_by_constructor[entry['constructorId']] += entry['points']
            by_constructor = defaultdict(list)
            for entry in constructor_scored:
                if entry['constructorId']:
                    by_constructor[entry['constructorId']].append(entry)
            for constructor_id, entries in by_constructor.items():
                constructor_meta[constructor_id] = constructor_id
                if best_car_only:
                    best = max(entries, key=lambda item: item['points'])
                    points = best['points']
                    if best['classifiedPosition']:
                        constructor_places[constructor_id][best['classifiedPosition']] += 1
                else:
                    points = sum(item['points'] for item in entries)
                    for item in entries:
                        if item['classifiedPosition']:
                            constructor_places[constructor_id][item['classifiedPosition']] += 1
                points += sprint_by_constructor.get(constructor_id, 0.0)
                constructor_results[constructor_id].append(points)
                constructor_rounds[constructor_id].append(round_number)

    driver_drop = (
        {'type': 'none'} if drop_mode == 'none'
        else drop_rule_for_year(season, 'drivers', data)
    )
    constructor_drop = (
        {'type': 'none'} if drop_mode == 'none'
        else drop_rule_for_year(season, 'constructors', data)
    )
    official_drivers = official_standings_map(season, 'driverStandings')
    official_constructors = official_standings_map(season, 'constructorStandings')

    def standings(
        results_map, sprint_map, places_map, meta, official, drop,
        rounds_map=None, fl_map=None,
    ):
        rows = []
        for key, values in results_map.items():
            combined = list(values) + list(sprint_map.get(key, []))
            fl_total = (fl_map or {}).get(key, 0.0)
            if drop and drop.get('type') != 'none':
                # Sprint points were not dropped historically; they count in full.
                # 1950–1959 fastest-lap points stay even when the race is dropped.
                gross, counted = apply_drop_rules(
                    values, drop, rounds=(rounds_map or {}).get(key),
                )
                counted += sum(sprint_map.get(key, [])) + fl_total
                gross += sum(sprint_map.get(key, [])) + fl_total
            else:
                gross = counted = sum(combined) + fl_total
            official_row = official.get(key) or {}
            rows.append({
                'id': key,
                'name': official_row.get('name') or meta.get(key) or key,
                'points': counted,
                'grossPoints': gross,
                'officialPoints': official_row.get('points'),
                'officialPosition': official_row.get('position'),
                'positionCounts': {
                    str(place): count
                    for place, count in sorted(places_map[key].items())
                },
            })
        rows.sort(
            key=lambda row: (
                -row['points'],
                countback_tuple({
                    int(place): count
                    for place, count in row['positionCounts'].items()
                }),
                row['name'],
            )
        )
        for index, row in enumerate(rows, start=1):
            row['position'] = index
        return rows

    return {
        'drivers': standings(
            driver_results, driver_sprint, driver_places, driver_meta,
            official_drivers, driver_drop,
            rounds_map=driver_rounds, fl_map=driver_fl,
        ),
        'constructors': standings(
            constructor_results, defaultdict(list), constructor_places,
            constructor_meta, official_constructors, constructor_drop,
            rounds_map=constructor_rounds,
        ),
        'unavailableFastestLap': unavailable_fl,
    }


def _exception_lookup(data=None):
    data = data or load_systems_file()
    lookup = {}
    for item in data.get('ownEraExceptions') or []:
        lookup[(int(item['season']), item['kind'], item['id'])] = item
    return lookup


def peel_fastest_lap_points(fl_points):
    """Keep many-way 1950s FL shares after a drop; peel solo/two-way with the race.

    1952 Ascari Italy (0.5) drops. 1954 British GP (1/7) stays.
    """
    points = float(fl_points or 0)
    if 0 < points <= 0.2:
        return points
    return 0.0


def season_in_progress(season, data=None):
    """True only for the live incomplete season, not every year >= a labeled year."""
    del data
    season = int(season)
    return (
        season == int(api_update.current_year)
        and not api_update.season_is_complete(season)
    )


def classify_own_era_mismatch(season, kind, row_id, data=None):
    data = data or load_systems_file()
    if season_in_progress(season, data):
        return {
            'documented': True,
            'cause': 'Season in progress; official standings can move race to race.',
        }
    item = _exception_lookup(data).get((int(season), kind, row_id))
    if not item:
        return {'documented': False, 'cause': 'unexplained'}
    return {
        'documented': True,
        'cause': item.get('cause') or item.get('note') or 'documented',
        'race': item.get('race'),
    }


def listed_rescored_seasons():
    if not os.path.isdir(RESCORED_DIR):
        return []
    found = []
    for name in os.listdir(RESCORED_DIR):
        if name.endswith('.json') and name[:-5].isdigit():
            found.append(int(name[:-5]))
    return sorted(found)


def write_rescored_files(seasons=None):
    data = load_systems_file()
    targets = seasons or api_update.listed_seasons()
    api_update.ensure_directory_exists(RESCORED_DIR)
    in_progress = {
        'year': int(api_update.current_year),
        'complete': api_update.season_is_complete(api_update.current_year),
        'note': (
            'Own-era totals can disagree with Jolpica while the current '
            'season is still running. A labeled year in scoringSystems.json '
            'does not document later seasons.'
        ),
    }
    index = {
        'seasons': [],
        'systems': [
            {'id': system['id'], 'label': system['label']}
            for system in systems_list(data)
        ],
        'variants': ['season', 'none'],
        'gaps': data.get('gaps') or {},
        'ownEraExceptions': data.get('ownEraExceptions') or [],
        'inProgressSeason': in_progress,
    }
    mismatches = []
    for season in targets:
        payload = {'season': season, 'systems': {}}
        era = system_for_year(season, data)
        for system in systems_list(data):
            payload['systems'][system['id']] = {}
            for drop_mode in ('season', 'none'):
                scored = rescore_season(season, system, drop_mode, data)
                payload['systems'][system['id']][drop_mode] = {
                    'drivers': scored['drivers'],
                    'constructors': scored['constructors'],
                }
                if drop_mode == 'season' and system['id'] == era['id']:
                    for kind in ('drivers', 'constructors'):
                        if kind == 'constructors' and season < 1958:
                            continue
                        for row in scored[kind]:
                            official = row.get('officialPoints')
                            if official is None:
                                continue
                            if abs(row['points'] - official) > POINTS_TOLERANCE:
                                classified = classify_own_era_mismatch(
                                    season, kind, row['id'], data
                                )
                                mismatches.append({
                                    'season': season,
                                    'kind': kind,
                                    'id': row['id'],
                                    'name': row['name'],
                                    'rescored': row['points'],
                                    'official': official,
                                    'cause': classified['cause'],
                                    'documented': classified['documented'],
                                    'race': classified.get('race'),
                                })
        api_update.write_json_atomic(f'{RESCORED_DIR}/{season}.json', payload)
        print(f'Rescored {season}')
    index['seasons'] = listed_rescored_seasons()
    api_update.write_json_atomic(SCORING_INDEX_FILE, index)
    return mismatches


def validate_scoring(seasons=None):
    data = load_systems_file()
    apply_scoring_overrides(seasons)
    targets = seasons or [
        year for year in api_update.listed_seasons() if year <= api_update.current_year
    ]
    fl_mismatches = []
    points_mismatches = []
    winner_anomalies = []
    era_1950 = system_for_year(1950, data)
    expected_half = {
        (int(item['season']), str(item['round']))
        for item in (data.get('shortenedRaces') or {}).get('halfPoints') or []
    }
    expected_double = {
        (int(item['season']), str(item['round']))
        for item in (data.get('shortenedRaces') or {}).get('doublePoints') or []
    }

    for season in targets:
        era = system_for_year(season, data)
        races = api_update.load_json(f'races/{season}/results.json', [])
        if 1950 <= season <= 1959:
            for race in races:
                shares = recover_fastest_lap_shares(season, race, era_1950, data)
                total = sum(shares.values())
                if shares and abs(total - 1.0) > 0.05:
                    fl_mismatches.append({
                        'season': season,
                        'round': race.get('round'),
                        'raceName': race.get('raceName'),
                        'shareSum': total,
                    })
        driver_sum = defaultdict(float)
        for race in races:
            for result in race.get('Results') or []:
                driver_id = _driver_id(result)
                if driver_id:
                    driver_sum[driver_id] += parse_points(result.get('points'))
            winner = next(
                (row for row in (race.get('Results') or []) if str(row.get('position')) == '1'),
                None,
            )
            if not winner:
                continue
            full = position_points(era.get('racePoints') or [], 1)
            awarded = parse_points(winner.get('points'))
            key = (int(season), str(race.get('round')))
            rows = race.get('Results') or []
            shared_win = sum(1 for row in rows if str(row.get('position')) == '1') > 1
            if key in expected_half:
                if abs(awarded - full * 0.5) > 0.6:
                    winner_anomalies.append({
                        'season': season, 'round': race.get('round'),
                        'raceName': race.get('raceName'), 'awarded': awarded,
                        'expected': 'half',
                    })
            elif key in expected_double:
                if abs(awarded - full * 2) > 1.0:
                    winner_anomalies.append({
                        'season': season, 'round': race.get('round'),
                        'raceName': race.get('raceName'), 'awarded': awarded,
                        'expected': 'double',
                    })
            elif awarded + 0.05 < full and not shared_win:
                winner_anomalies.append({
                    'season': season, 'round': race.get('round'),
                    'raceName': race.get('raceName'), 'awarded': awarded,
                    'expected': 'full',
                })
        sprints = api_update.load_json(f'races/{season}/sprint.json', [])
        for race in sprints:
            for result in race.get('SprintResults') or []:
                driver_id = _driver_id(result)
                if driver_id:
                    driver_sum[driver_id] += parse_points(result.get('points'))
        official = official_standings_map(season, 'driverStandings')
        drop = drop_rule_for_year(season, 'drivers', data)
        for driver_id, gross in driver_sum.items():
            if driver_id not in official:
                continue
            if drop and drop.get('type') != 'none':
                continue
            official_points = official[driver_id]['points']
            if abs(gross - official_points) > POINTS_TOLERANCE:
                points_mismatches.append({
                    'season': season,
                    'driverId': driver_id,
                    'summed': gross,
                    'standings': official_points,
                })

    print('=== Fastest-lap share sums (1950-1959) not equal to 1 ===')
    if not fl_mismatches:
        print('none')
    for item in fl_mismatches:
        print(item)
    print('=== Driver summed race points vs final standings (no-drop seasons) ===')
    if not points_mismatches:
        print('none')
    for item in points_mismatches[:50]:
        print(item)
    if len(points_mismatches) > 50:
        print(f'... {len(points_mismatches) - 50} more')
    print('=== Winner not paid that era\'s full points ===')
    print(
        f'half-points races listed: {len(expected_half)} expected 6; '
        f'mis-paid: {sum(1 for item in winner_anomalies if (item["season"], str(item["round"])) in expected_half)}'
    )
    print(
        f'double-points races listed: {len(expected_double)} expected 1; '
        f'mis-paid: {sum(1 for item in winner_anomalies if (item["season"], str(item["round"])) in expected_double)}'
    )
    unexpected = [
        item for item in winner_anomalies
        if (item['season'], str(item['round'])) not in expected_half
        and (item['season'], str(item['round'])) not in expected_double
    ]
    print(f'unexpected short-pay races: {len(unexpected)}')
    for item in unexpected:
        print(item)
    return {
        'fastestLap': fl_mismatches,
        'points': points_mismatches,
        'winners': winner_anomalies,
    }
