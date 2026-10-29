"""Publish circuit guides: hand-written content plus records from the race files.

circuits/content/{circuitId}.json   hand-written: layouts, corners, moments, history, sources
circuits/{circuitId}.json           published guide: content merged with computed stats
circuits/index.json                 one card per circuit that has hosted a championship race
circuits/sources.json               every source and video, grouped by URL

Everything that can change after a race (lap and pole records, race laps, wins,
poles, next race) is worked out from races/{season}/*.json on every run, so the
content files only hold what the race data can't know: track length, turns,
elevation, corner names, layout eras and the writing.

Records only count races on the layout they were set on. A layout covers a
season range, and can claim single rounds (the 2020 Sakhir GP ran Bahrain's
outer loop). Seasons no layout claims fall into an implicit "earlier" layout.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

import api_update
from poles import is_sprint_weekend, pole_sitter
from scoring import is_indy_500

SCHEMA_VERSION = 1
CIRCUITS_DIR = 'circuits'
CONTENT_DIR = os.path.join(CIRCUITS_DIR, 'content')
RACES_DIR = 'races'
# Race fastest-lap times start in 2004. Qualifying times are complete from
# 2003; 1994-2002 has only some rounds.
LAP_TIMES_FROM = 2004
QUALIFYING_TIMES_FROM = 2003
TOP_N = 5

COVERAGE_NOTES = {
    'lapRecord': (
        f'Race lap records count races from {LAP_TIMES_FROM} on: the race data '
        f'has no fastest-lap times before {LAP_TIMES_FROM}.'
    ),
    'poleRecord': (
        f'Pole records use the pole-winning qualifying lap. Qualifying times are '
        f'complete from {QUALIFYING_TIMES_FROM}; 1994-{QUALIFYING_TIMES_FROM - 1} '
        f'has some rounds only.'
    ),
    'championship': (
        'Race counts, wins and poles cover World Championship Grands Prix only, '
        'not non-championship races. The Indianapolis 500 (1950-1960) is left '
        'out of wins and poles.'
    ),
    'raceLaps': (
        'Race laps is the scheduled distance: the lap count most winners on the '
        'layout completed, so shortened races don\'t change it.'
    ),
    'missingTimes': (
        'missingTimes lists races on a layout whose lap or pole time is missing '
        'from the race data; a record may be faster than shown until they are filled.'
    ),
}


# ---------------------------------------------------------------- race data

def lap_seconds(text):
    """'1:12.909' -> 72.909. Returns None for blanks and malformed times."""
    if not text:
        return None
    try:
        parts = str(text).strip().split(':')
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        return None


def _name(row):
    driver = row.get('Driver') or {}
    return f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()


def _person(row):
    driver = row.get('Driver') or {}
    constructor = row.get('Constructor') or {}
    return {
        'driverId': driver.get('driverId'),
        'name': _name(row),
        'constructorId': constructor.get('constructorId'),
        'constructor': constructor.get('name'),
    }


def _int(value):
    text = str(value if value is not None else '')
    return int(text) if text.isdigit() else None


def _seasons(root):
    directory = os.path.join(root, RACES_DIR)
    if not os.path.isdir(directory):
        return []
    return sorted(int(name) for name in os.listdir(directory) if name.isdigit())


def _load(root, season, name):
    return api_update.load_json(os.path.join(root, RACES_DIR, str(season), f'{name}.json'), [])


def fastest_lap(race):
    """Fastest race lap in a Grand Prix: {time, seconds, lap, ...driver} or None."""
    best = None
    for row in race.get('Results') or []:
        lap = row.get('FastestLap') or {}
        time = (lap.get('Time') or {}).get('time')
        seconds = lap_seconds(time)
        if seconds is None:
            continue
        if best is None or seconds < best['seconds']:
            best = {'time': time, 'seconds': seconds, 'lap': _int(lap.get('lap')), **_person(row)}
    return best


def pole_lap(qualifying_race, driver_id):
    """The pole sitter's lap from the last qualifying session they ran.

    Follows the official pole sitter, not qualifying P1: a driver who topped
    qualifying and took a grid penalty didn't take pole with that lap.
    """
    for row in (qualifying_race or {}).get('QualifyingResults') or []:
        if (row.get('Driver') or {}).get('driverId') != driver_id:
            continue
        for session in ('Q3', 'Q2', 'Q1'):
            seconds = lap_seconds(row.get(session))
            if seconds is not None:
                return {'time': row[session], 'seconds': seconds, 'session': session, **_person(row)}
    return None


def load_races(root='.'):
    """Every held Grand Prix, grouped by circuitId, oldest first."""
    by_circuit = defaultdict(list)
    for season in _seasons(root):
        details = {str(r.get('round')): r for r in _load(root, season, 'raceDetails')}
        qualifying = {str(r.get('round')): r for r in _load(root, season, 'qualifying')}
        for race in _load(root, season, 'results'):
            results = race.get('Results') or []
            if not results:
                continue
            round_key = str(race.get('round'))
            circuit = race.get('Circuit') or {}
            winner = next((r for r in results if str(r.get('position')) == '1'), results[0])
            indy = is_indy_500(race)
            pole = None if indy else pole_sitter(
                season, race, qualifying.get(round_key),
                sprint_weekend=is_sprint_weekend(details.get(round_key)),
            )
            by_circuit[circuit.get('circuitId')].append({
                'season': season,
                'round': _int(race.get('round')),
                'raceName': race.get('raceName'),
                'date': race.get('date'),
                'indy500': indy,
                'circuit': circuit,
                'winner': _person(winner),
                'winnerLaps': _int(winner.get('laps')),
                'pole': {k: pole[k] for k in ('driverId', 'name', 'constructorId')} if pole else None,
                'fastestLap': fastest_lap(race),
                'poleLap': pole_lap(qualifying.get(round_key), pole['driverId']) if pole else None,
            })
    return by_circuit


def load_calendar(root='.', season=None):
    """This season's calendar rounds, with whether each has results yet."""
    seasons = _seasons(root)
    if not seasons:
        return []
    season = season or seasons[-1]
    held = {str(r.get('round')) for r in _load(root, season, 'results') if r.get('Results')}
    return [
        {
            'season': season,
            'round': _int(r.get('round')),
            'raceName': r.get('raceName'),
            'date': r.get('date'),
            'time': r.get('time'),
            'circuit': r.get('Circuit') or {},
            'held': str(r.get('round')) in held,
        }
        for r in _load(root, season, 'raceDetails')
    ]


# ------------------------------------------------------------------ layouts

def load_content(root='.'):
    directory = os.path.join(root, CONTENT_DIR)
    content = {}
    if not os.path.isdir(directory):
        return content
    for name in sorted(os.listdir(directory)):
        if name.endswith('.json'):
            data = api_update.load_json(os.path.join(directory, name), None)
            if data:
                content[data['circuitId']] = data
    return content


def validate_content(content):
    """Problems that would make records wrong. Empty list means it's fine."""
    problems = []
    for circuit_id, data in content.items():
        seen_seasons = {}
        seen_rounds = {}
        for layout in data.get('layouts') or []:
            layout_id = layout.get('id')
            if layout_id == 'earlier':
                problems.append(f'{circuit_id}: "earlier" is reserved for unclaimed seasons')
            for season, round_number in (tuple(r) for r in layout.get('rounds') or []):
                key = (season, round_number)
                if key in seen_rounds:
                    problems.append(f'{circuit_id}: {season} round {round_number} claimed by {seen_rounds[key]} and {layout_id}')
                seen_rounds[key] = layout_id
            if layout.get('rounds'):
                continue
            start, end = layout.get('from'), layout.get('to') or 9999
            if start is None or start > end:
                problems.append(f'{circuit_id}: layout {layout_id} has a bad season range')
                continue
            for season in range(start, min(end, 2100) + 1):
                if season in seen_seasons:
                    problems.append(f'{circuit_id}: {season} claimed by {seen_seasons[season]} and {layout_id}')
                    break
                seen_seasons[season] = layout_id
    return problems


def layout_for(layouts, season, round_number):
    for layout in layouts:
        if [season, round_number] in [list(r) for r in layout.get('rounds') or []]:
            return layout['id']
    for layout in layouts:
        if layout.get('rounds'):
            continue
        if layout['from'] <= season <= (layout.get('to') or 9999):
            return layout['id']
    return 'earlier'


# -------------------------------------------------------------------- stats

def _record(races, key, data_from):
    best = None
    for race in races:
        lap = race[key]
        if race['season'] < data_from or not lap:
            continue
        # Equal times: the first to set it keeps the record.
        if best is None or lap['seconds'] < best['seconds']:
            best = {**lap, 'season': race['season'], 'round': race['round'], 'raceName': race['raceName']}
    if best:
        best.pop('seconds')
    return best


def _scheduled_laps(races):
    laps = Counter(r['winnerLaps'] for r in races if r['winnerLaps'])
    if not laps:
        return None
    # Most common wins; ties go to the most recent race's count.
    top = max(laps.values())
    for race in reversed(races):
        if laps.get(race['winnerLaps']) == top:
            return race['winnerLaps']
    return None


def _leaders(races, pick, id_key, name_key):
    counts = Counter()
    names = {}
    for race in races:
        entry = pick(race)
        if not entry or not entry.get(id_key):
            continue
        counts[entry[id_key]] += 1
        names[entry[id_key]] = entry.get(name_key)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], names[item[0]] or ''))
    if not ranked:
        return []
    cutoff = ranked[min(TOP_N, len(ranked)) - 1][1]
    return [
        {id_key: key, name_key: names[key], 'count': count}
        for key, count in ranked if count >= cutoff
    ]


def _season_spans(seasons):
    """[1950, 1951, 1952, 1955] -> [[1950, 1952], [1955, 1955]]."""
    spans = []
    for season in sorted(set(seasons)):
        if spans and season == spans[-1][1] + 1:
            spans[-1][1] = season
        else:
            spans.append([season, season])
    return spans


def layout_stats(layout, races):
    notes = []
    held = [r for r in races if not r['indy500']]
    if held and held[0]['season'] < LAP_TIMES_FROM:
        notes.append('lapRecord')
    if held and held[0]['season'] < QUALIFYING_TIMES_FROM:
        notes.append('poleRecord')
    return {
        **{k: v for k, v in layout.items() if k not in ('rounds',)},
        'seasons': _season_spans(r['season'] for r in races),
        'raceCount': len(races),
        'raceLaps': _scheduled_laps(held),
        'lapRecord': _record(held, 'fastestLap', LAP_TIMES_FROM),
        'poleRecord': _record(held, 'poleLap', QUALIFYING_TIMES_FROM),
        # Races inside the data window that still have no time: the record
        # may be missing a faster lap from one of these.
        'missingTimes': {
            'lapRecord': [[r['season'], r['round']] for r in held
                          if r['season'] >= LAP_TIMES_FROM and not r['fastestLap']],
            'poleRecord': [[r['season'], r['round']] for r in held
                           if r['season'] >= QUALIFYING_TIMES_FROM and not r['poleLap']],
        },
        'notes': notes,
    }


def circuit_status(circuit_id, races, calendar, content):
    if (content or {}).get('retired'):
        return 'historical'
    if any(r['circuit'].get('circuitId') == circuit_id for r in calendar):
        return 'current'
    if calendar and races and races[-1]['season'] >= calendar[0]['season'] - 1:
        return 'current'
    return 'historical'


def next_race(circuit_id, calendar):
    for race in calendar:
        if race['circuit'].get('circuitId') == circuit_id and not race['held']:
            return {k: race[k] for k in ('season', 'round', 'raceName', 'date', 'time')}
    return None


def _race_row(race, layout_id):
    return {
        'season': race['season'],
        'round': race['round'],
        'raceName': race['raceName'],
        'date': race['date'],
        'layoutId': layout_id,
        'winner': race['winner'],
        'pole': race['pole'],
        'indy500': race['indy500'],
    }


def _location(circuit):
    location = circuit.get('Location') or {}
    return {
        'locality': location.get('locality'),
        'country': location.get('country'),
        'lat': float(location['lat']) if location.get('lat') else None,
        'long': float(location['long']) if location.get('long') else None,
    }


def build_circuit(circuit_id, races, calendar, content=None):
    content = content or {}
    layouts = content.get('layouts') or []
    by_layout = defaultdict(list)
    rows = []
    for race in races:
        layout_id = layout_for(layouts, race['season'], race['round'])
        by_layout[layout_id].append(race)
        rows.append(_race_row(race, layout_id))

    layout_rows = [layout_stats(layout, by_layout.get(layout['id'], [])) for layout in layouts]
    if by_layout.get('earlier'):
        layout_rows.append(layout_stats({'id': 'earlier', 'name': 'Earlier layouts'}, by_layout['earlier']))
    latest_layout = rows[-1]['layoutId'] if rows else (layouts[-1]['id'] if layouts else None)

    championship = [r for r in races if not r['indy500']]
    circuit = (races[-1]['circuit'] if races else {}) or {}
    notes = {n for row in layout_rows for n in row['notes']} | {'championship', 'raceLaps'}
    if any(row['missingTimes']['lapRecord'] or row['missingTimes']['poleRecord'] for row in layout_rows):
        notes.add('missingTimes')
    notes = sorted(notes)
    guide_keys = ('summary', 'character', 'corners', 'moments', 'history', 'leftCalendar')
    return {
        'schemaVersion': SCHEMA_VERSION,
        'circuitId': circuit_id,
        'name': content.get('name') or circuit.get('circuitName'),
        'shortName': content.get('shortName'),
        'officialName': circuit.get('circuitName'),
        'location': _location(circuit),
        'wikipedia': circuit.get('url'),
        'status': circuit_status(circuit_id, races, calendar, content),
        'hasGuide': bool(content),
        'currentLayoutId': latest_layout,
        'nextRace': next_race(circuit_id, calendar),
        'championship': {
            'firstSeason': championship[0]['season'] if championship else None,
            'lastSeason': championship[-1]['season'] if championship else None,
            'seasons': _season_spans(r['season'] for r in championship),
            'raceCount': len(championship),
            'lastWinner': {**championship[-1]['winner'], 'season': championship[-1]['season']} if championship else None,
            'mostWins': _leaders(championship, lambda r: r['winner'], 'driverId', 'name'),
            'mostConstructorWins': _leaders(championship, lambda r: r['winner'], 'constructorId', 'constructor'),
            'mostPoles': _leaders(championship, lambda r: r['pole'], 'driverId', 'name'),
        },
        'layouts': layout_rows,
        'guide': {key: content.get(key) for key in guide_keys if content.get(key) is not None},
        'sources': content.get('sources') or [],
        'notes': {key: COVERAGE_NOTES[key] for key in notes},
        'races': rows,
    }


def build_index(circuits, data_through):
    cards = []
    for circuit in circuits.values():
        current = next((l for l in circuit['layouts'] if l['id'] == circuit['currentLayoutId']), {})
        cards.append({
            'circuitId': circuit['circuitId'],
            'name': circuit['name'],
            'shortName': circuit['shortName'],
            'location': circuit['location'],
            'status': circuit['status'],
            'hasGuide': circuit['hasGuide'],
            'lengthKm': current.get('lengthKm'),
            'turns': current.get('turns'),
            'firstSeason': circuit['championship']['firstSeason'],
            'lastSeason': circuit['championship']['lastSeason'],
            'raceCount': circuit['championship']['raceCount'],
            'lastWinner': circuit['championship']['lastWinner'],
            'nextRace': circuit['nextRace'],
        })
    cards.sort(key=lambda c: (
        c['status'] != 'current',
        (c['nextRace'] or {}).get('date') or '9999',
        c['name'] or '',
    ))
    return {
        'schemaVersion': SCHEMA_VERSION,
        'dataThrough': data_through,
        'files': {
            'circuit': 'circuits/{circuitId}.json',
            'sources': 'circuits/sources.json',
        },
        'notes': COVERAGE_NOTES,
        'circuits': cards,
    }


def build_sources(content):
    """All sources grouped by URL, plus every moment video."""
    sources = {}
    videos = []
    for circuit_id, data in sorted(content.items()):
        for source in data.get('sources') or []:
            entry = sources.setdefault(source['url'], {
                'url': source['url'],
                'title': source.get('title'),
                'publisher': source.get('publisher'),
                'circuits': [],
            })
            entry['circuits'].append(circuit_id)
        for moment in data.get('moments') or []:
            if moment.get('video'):
                videos.append({
                    'url': moment['video'],
                    'title': moment.get('title'),
                    'season': moment.get('season'),
                    'circuitId': circuit_id,
                })
    ordered = sorted(sources.values(), key=lambda s: ((s['publisher'] or '').lower(), (s['title'] or '').lower()))
    return {'schemaVersion': SCHEMA_VERSION, 'sources': ordered, 'videos': videos}


def _data_through(by_circuit):
    latest = max(
        ((r['season'], r['round'], r['raceName']) for races in by_circuit.values() for r in races),
        default=None,
    )
    return {'season': latest[0], 'round': latest[1], 'raceName': latest[2]} if latest else None


def build_all(root='.'):
    content = load_content(root)
    problems = validate_content(content)
    by_circuit = load_races(root)
    for circuit_id in content:
        if circuit_id not in by_circuit:
            by_circuit[circuit_id] = []
    calendar = load_calendar(root)
    for race in calendar:
        by_circuit.setdefault(race['circuit'].get('circuitId'), [])
    missing = sorted({r['circuit'].get('circuitId') for r in calendar} - set(content))
    circuits = {
        circuit_id: build_circuit(circuit_id, races, calendar, content.get(circuit_id))
        for circuit_id, races in sorted(by_circuit.items()) if circuit_id
    }
    # A circuit on the calendar that hasn't raced yet still needs a name.
    for race in calendar:
        circuit = circuits[race['circuit'].get('circuitId')]
        circuit['name'] = circuit['name'] or race['circuit'].get('circuitName')
        circuit['officialName'] = circuit['officialName'] or race['circuit'].get('circuitName')
        if not circuit['location']['country']:
            circuit['location'] = _location(race['circuit'])
    return {
        'circuits': circuits,
        'index': build_index(circuits, _data_through(by_circuit)),
        'sources': build_sources(content),
        'problems': problems,
        'missingContent': missing,
        'warnings': build_warnings(by_circuit, circuits, missing),
    }


def build_warnings(by_circuit, circuits, missing_content):
    """Things a person should look at after the latest race.

    Only the most recent race is checked, so a warning shows on the runs after
    that race and clears once the next one is in.
    - Its winner's lap count differs from the layout's usual count: either the
      race was shortened or the track changed and needs a new layout entry.
    - It has no fastest-lap or pole time, so a record could be missing a lap.
    - A circuit on this season's calendar has no guide content.
    """
    warnings = [
        f'{circuit_id} is on this season\'s calendar but has no guide: add circuits/content/{circuit_id}.json.'
        for circuit_id in missing_content
    ]
    latest = max(
        ((race, circuit_id) for circuit_id, races in by_circuit.items() for race in races if circuit_id),
        key=lambda item: (item[0]['season'], item[0]['round']),
        default=None,
    )
    if not latest:
        return warnings
    race, circuit_id = latest
    if race['indy500']:
        return warnings
    label = f"{race['season']} {race['raceName']} ({circuit_id})"
    circuit = circuits[circuit_id]
    layout_id = next(r['layoutId'] for r in circuit['races']
                     if (r['season'], r['round']) == (race['season'], race['round']))
    layout = next(l for l in circuit['layouts'] if l['id'] == layout_id)
    if race['winnerLaps'] and layout['raceLaps'] and race['winnerLaps'] != layout['raceLaps']:
        warnings.append(
            f"{label}: the winner did {race['winnerLaps']} laps; races on layout {layout_id} usually run "
            f"{layout['raceLaps']}. If the track changed, add a layout to circuits/content/{circuit_id}.json "
            f"so records don't mix. If the race was shortened, ignore this."
        )
    if race['season'] >= LAP_TIMES_FROM and not race['fastestLap']:
        warnings.append(f'{label}: no fastest-lap time in the race data, so the lap record may be missing a lap.')
    if race['season'] >= QUALIFYING_TIMES_FROM and race['pole'] and not race['poleLap']:
        warnings.append(f'{label}: no pole lap time in the qualifying data, so the pole record may be missing a lap.')
    return warnings


def print_warnings(summary):
    """GitHub Actions shows ::warning lines on the run's summary page."""
    for warning in summary.get('warnings') or []:
        print(f'::warning title=Circuits::{warning}')


def _write_if_changed(path, payload):
    if api_update.load_json(path, None) == payload:
        return False
    api_update.write_json_atomic(path, payload)
    return True


def publish_circuits(write=True, root='.'):
    """Build every circuit file. Raises on layout problems before writing."""
    built = build_all(root)
    if built['problems']:
        raise RuntimeError('Circuit content problems:\n' + '\n'.join(built['problems']))
    written = 0
    if write:
        for circuit_id, circuit in built['circuits'].items():
            written += _write_if_changed(os.path.join(root, CIRCUITS_DIR, f'{circuit_id}.json'), circuit)
        written += _write_if_changed(os.path.join(root, CIRCUITS_DIR, 'index.json'), built['index'])
        written += _write_if_changed(os.path.join(root, CIRCUITS_DIR, 'sources.json'), built['sources'])
    return {
        'circuits': len(built['circuits']),
        'withGuides': sum(1 for c in built['circuits'].values() if c['hasGuide']),
        'filesWritten': written,
        'calendarCircuitsWithoutGuide': built['missingContent'],
        'dataThrough': built['index']['dataThrough'],
        'warnings': built['warnings'],
    }


if __name__ == '__main__':
    result = publish_circuits(write=True)
    print(json.dumps(result, indent=2))
    print_warnings(result)
