"""Outcome and cause labels for race results.

Jolpica describes a result with ``positionText`` (a number, or R/N/W/F/D/E)
and a free-text ``status`` (139 distinct strings so far). This module reduces
the pair to a small vocabulary the stats code and the site can reason about:

outcome  finished | lapped | retired | dsq | dns | dnq | withdrew
cause    mechanical | accident | contact | tyre | operational | driver | unknown

The vocabulary lives in statusCategories.json; retirementOverrides.json adds
causes for rows Jolpica records only as 'Retired'.
"""

from __future__ import annotations

import re

import api_update

STATUS_FILE = 'statusCategories.json'
RETIREMENT_OVERRIDES_FILE = 'retirementOverrides.json'

OUTCOMES = ('finished', 'lapped', 'retired', 'dsq', 'dns', 'dnq', 'withdrew')
CAUSES = (
    'mechanical', 'accident', 'contact', 'tyre', 'operational', 'driver', 'unknown',
)
STARTED_OUTCOMES = frozenset({'finished', 'lapped', 'retired', 'dsq'})
CLASSIFIED_OUTCOMES = frozenset({'finished', 'lapped'})

_cache = {}


def reset_cache():
    _cache.clear()


def load_status_categories():
    if 'categories' not in _cache:
        data = api_update.load_json(STATUS_FILE, None)
        if not data:
            raise RuntimeError(f'Missing {STATUS_FILE}')
        lookup = {}
        for kind, names in (data.get('kinds') or {}).items():
            for name in names:
                lookup[name] = (kind, None)
        for cause, names in (data.get('retirements') or {}).items():
            for name in names:
                if name in lookup:
                    raise RuntimeError(f'{STATUS_FILE} lists {name!r} twice')
                lookup[name] = ('retirement', cause)
        _cache['categories'] = {
            'lookup': lookup,
            'lapped': re.compile(data.get('lappedPattern') or r'^\+\d+ Laps?$'),
        }
    return _cache['categories']


def load_retirement_overrides():
    if 'overrides' not in _cache:
        data = api_update.load_json(RETIREMENT_OVERRIDES_FILE, {}) or {}
        overrides = {}
        for row in data.get('overrides') or []:
            cause = row.get('cause')
            if cause not in CAUSES:
                raise RuntimeError(
                    f'{RETIREMENT_OVERRIDES_FILE}: bad cause {cause!r} for {row}'
                )
            key = (str(row.get('season')), str(row.get('round')), row.get('driverId'))
            overrides[key] = row
        _cache['overrides'] = overrides
    return _cache['overrides']


def status_info(status):
    """(kind, cause, known) for a raw status string.

    kind is one of finished/lapped/dsq/dns/dnq/withdrew/retirement. Statuses
    not in statusCategories.json are retirements with an unknown cause.
    """
    text = str(status or '').strip()
    categories = load_status_categories()
    if categories['lapped'].match(text):
        return 'lapped', None, True
    if text in categories['lookup']:
        kind, cause = categories['lookup'][text]
        return kind, cause, True
    return 'retirement', 'unknown', False


def did_not_finish(status):
    """Status-only DNF check for callers without positionText.

    Classified retirements (an 'Engine' with a finishing position) count;
    '+N Laps', 'Lapped', disqualifications and non-starts do not.
    """
    kind, _, _ = status_info(status)
    return kind == 'retirement'


def classify(result, season=None, round_number=None):
    """Label one Jolpica result row.

    Returns a dict with outcome, cause, classified, started, dnf and
    statusKnown. positionText decides the outcome; status refines it and
    supplies the cause. Rows without positionText (older fixtures) fall back
    to the status alone.
    """
    text = str(result.get('positionText') or '').strip()
    status = result.get('status')
    kind, cause, known = status_info(status)

    if text.isdigit():
        # A classified driver started the race, whatever the status says.
        outcome = kind if kind in CLASSIFIED_OUTCOMES else 'retired'
    elif text == 'R':
        outcome = 'retired'
    elif text == 'N':
        # Not classified: running at the flag but too few laps, or stopped late.
        outcome = 'lapped' if kind in CLASSIFIED_OUTCOMES else 'retired'
    elif text == 'W':
        outcome = 'dns' if kind == 'dns' else 'withdrew'
    elif text == 'F':
        outcome = 'dnq'
    elif text in ('D', 'E'):
        outcome = 'dsq'
    else:
        outcome = 'retired' if kind == 'retirement' else kind

    if outcome == 'retired':
        cause = cause or 'unknown'
    elif kind != 'retirement':
        cause = None

    driver_id = (result.get('Driver') or {}).get('driverId')
    if season is not None and round_number is not None and driver_id:
        override = load_retirement_overrides().get(
            (str(season), str(round_number), driver_id)
        )
        if override:
            cause = override['cause']

    return {
        'outcome': outcome,
        'cause': cause,
        'classified': text.isdigit() or (not text and outcome in CLASSIFIED_OUTCOMES),
        'started': outcome in STARTED_OUTCOMES,
        'dnf': outcome == 'retired',
        'statusKnown': known,
    }


def is_dnf(result):
    return classify(result)['dnf']


def started(result):
    return classify(result)['started']
