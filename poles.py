"""Single pole-sitter rule used by driver files, team records and leaderboards."""

from __future__ import annotations


def _entry(row, source):
    driver = row.get('Driver') or {}
    constructor = row.get('Constructor') or {}
    return {
        'driverId': driver.get('driverId'),
        'constructorId': constructor.get('constructorId'),
        'name': f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
        'source': source,
    }


def qualifying_p1(qualifying_race):
    for row in (qualifying_race or {}).get('QualifyingResults') or []:
        if str(row.get('position')) == '1':
            return _entry(row, 'qualifying')
    return None


def grid_1(race):
    for row in (race or {}).get('Results') or []:
        if str(row.get('grid')) == '1':
            return _entry(row, 'grid')
    return None


def is_sprint_weekend(race_details_entry):
    return bool((race_details_entry or {}).get('Sprint'))


def pole_sitter(season, race, qualifying_race=None, sprint_weekend=False):
    """Official pole: the driver who started the Grand Prix from grid 1.

    Exceptions:
    - 2022 sprint weekends: Friday qualifying P1 (the sprint set the grid
      that year but did not decide pole).
    - Nobody started from grid 1: fall back to qualifying P1.
    """
    season = int(season)
    if season == 2022 and sprint_weekend:
        return qualifying_p1(qualifying_race) or grid_1(race)
    return grid_1(race) or qualifying_p1(qualifying_race)
