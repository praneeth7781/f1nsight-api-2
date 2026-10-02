import argparse
import requests, json, os, datetime, math, numpy as np, shutil, tempfile
import time
from datetime import datetime as dt
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

api_url = 'https://api.jolpi.ca/ergast/f1'
# api_url = 'http://ergast.com/api/f1'
current_year = datetime.date.today().year
api_request_delay_seconds = float(os.environ.get('F1NSIGHT_API_DELAY_SECONDS', '1.0'))
api_max_retries = int(os.environ.get('F1NSIGHT_API_MAX_RETRIES', '6'))
api_timeout_seconds = (10, 60)

api_session = requests.Session()
api_session.headers.update({
    'User-Agent': 'f1nsight-api-updater/1.0'
})
last_api_request_at = 0.0
PAGE_LIMIT = 100
ROUND_RESULT_KEYS = {
    'results': 'Results',
    'qualifying': 'QualifyingResults',
    'sprint': 'SprintResults',
}


def api_get(url):
    """GET an API URL slowly and retry transient failures.

    A failed request raises instead of allowing the workflow to commit partial
    data. The one-request-per-second default is intentionally conservative.
    """
    global last_api_request_at

    for attempt in range(1, api_max_retries + 1):
        elapsed = time.monotonic() - last_api_request_at
        if elapsed < api_request_delay_seconds:
            time.sleep(api_request_delay_seconds - elapsed)

        last_api_request_at = time.monotonic()
        try:
            response = api_session.get(url, timeout=api_timeout_seconds)
        except requests.RequestException as exc:
            if attempt == api_max_retries:
                raise RuntimeError(
                    f'API request failed after {api_max_retries} attempts: {url}'
                ) from exc

            sleep_for = min(2 ** (attempt - 1), 60)
            print(
                f'API request error for {url}: {exc}. '
                f'Waiting {sleep_for}s before retry {attempt}/{api_max_retries}.'
            )
            time.sleep(sleep_for)
            continue

        if response.status_code == 200:
            return response

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == api_max_retries:
                raise RuntimeError(
                    f'API request failed after {api_max_retries} attempts '
                    f'(status {response.status_code}): {url}'
                )

            retry_after = response.headers.get('Retry-After')
            try:
                sleep_for = max(float(retry_after), api_request_delay_seconds)
            except (TypeError, ValueError):
                sleep_for = min(max(5, 2 ** attempt), 90)

            print(
                f'API returned {response.status_code} for {url}. '
                f'Waiting {sleep_for:g}s before retry '
                f'{attempt}/{api_max_retries}.'
            )
            time.sleep(sleep_for)
            continue

        raise RuntimeError(
            f'API returned non-retryable status {response.status_code}: {url}'
        )

    raise RuntimeError(f'API request unexpectedly exhausted retries: {url}')


def jolpica_page_url(url, limit=PAGE_LIMIT, offset=0):
    """Attach Jolpica limit/offset without dropping any existing query params."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['limit'] = str(limit)
    query['offset'] = str(offset)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _mrdata_table(mrdata):
    if 'RaceTable' in mrdata:
        return 'RaceTable', 'Races'
    if 'StandingsTable' in mrdata:
        return 'StandingsTable', 'StandingsLists'
    if 'ConstructorTable' in mrdata:
        return 'ConstructorTable', 'Constructors'
    if 'DriverTable' in mrdata:
        return 'DriverTable', 'Drivers'
    return None, None


def _merge_named_lists(pages, item_keys):
    """Merge paginated copies of the same parent object by season/round."""
    merged = {}
    order = []
    for page in pages:
        for item in page:
            key = (str(item.get('season', '')), str(item.get('round', '')))
            if key not in merged:
                merged[key] = item
                order.append(key)
                continue
            existing = merged[key]
            for list_key in item_keys:
                extra = item.get(list_key)
                if extra:
                    existing.setdefault(list_key, [])
                    existing[list_key].extend(extra)
    return [merged[key] for key in order]


def _decode_json(response, url):
    try:
        return response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError(f'API returned invalid JSON: {url}') from exc


def api_get_json(url, paginate=True):
    """GET JSON and follow Jolpica offset pages until MRData.total is reached."""
    first_url = jolpica_page_url(url) if paginate else url
    payload = _decode_json(api_get(first_url), first_url)
    if not paginate:
        return payload

    mrdata = payload.get('MRData') or {}
    try:
        total = int(mrdata.get('total') or 0)
        limit = int(mrdata.get('limit') or PAGE_LIMIT)
        offset = int(mrdata.get('offset') or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'API returned invalid pagination metadata: {first_url}') from exc

    table_key, list_key = _mrdata_table(mrdata)
    if table_key is None:
        return payload

    pages = [mrdata.get(table_key, {}).get(list_key, []) or []]
    next_offset = offset + limit
    while next_offset < total:
        page_url = jolpica_page_url(url, limit=PAGE_LIMIT, offset=next_offset)
        page_payload = _decode_json(api_get(page_url), page_url)
        page_list = (
            (page_payload.get('MRData') or {})
            .get(table_key, {})
            .get(list_key, [])
        ) or []
        pages.append(page_list)
        next_offset += PAGE_LIMIT

    if table_key == 'RaceTable':
        merged = _merge_named_lists(
            pages,
            ('Results', 'QualifyingResults', 'SprintResults'),
        )
    elif table_key == 'StandingsTable':
        merged = _merge_named_lists(
            pages,
            ('DriverStandings', 'ConstructorStandings'),
        )
    else:
        merged = []
        for page in pages:
            merged.extend(page)
    payload['MRData'][table_key][list_key] = merged
    payload['MRData']['total'] = str(total)
    payload['MRData']['offset'] = '0'
    return payload


def api_races(url):
    """Return RaceTable.Races or fail without modifying stored data."""
    data = api_get_json(url)
    races = data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
    if not races:
        raise RuntimeError(f'API returned no race data: {url}')
    try:
        total = int((data.get('MRData') or {}).get('total') or 0)
    except (TypeError, ValueError):
        total = 0
    for race in races:
        race['_mrdataTotal'] = total
    return races


def parse_points(value):
    """Keep fractional championship points. Never truncate with int()."""
    if value in (None, '', '-'):
        return 0.0
    return float(value)


def load_json(file_path, default):
    if not os.path.exists(file_path):
        return default
    with open(file_path, 'r', encoding='utf-8') as file:
        return json.load(file)


def write_json_atomic(file_path, data):
    """Replace a JSON file only after the complete new document is written."""
    directory = os.path.dirname(file_path) or '.'
    ensure_directory_exists(directory)
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f'.{os.path.basename(file_path)}.',
        suffix='.tmp',
        dir=directory,
    )
    try:
        with os.fdopen(file_descriptor, 'w', encoding='utf-8') as file:
            json.dump(
                data,
                file,
                indent=4,
                ensure_ascii=False,
                cls=NpEncoder,
            )
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, file_path)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def merge_round_records(existing, additions):
    """Append new rounds while preserving every record already stored."""
    merged = list(existing)
    known_rounds = {
        str(record.get('round'))
        for record in existing
        if record.get('round') is not None
    }
    for record in additions:
        round_number = str(record.get('round'))
        if round_number not in known_rounds:
            merged.append(record)
            known_rounds.add(round_number)
    merged.sort(key=lambda record: int(record.get('round', 10**9)))
    return merged

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

def update_constructors():
    season = current_year
    response = api_get(f'{api_url}/{season}/constructors.json')
    if response.status_code==200:
        responsedata = response.json()
        constructors = responsedata["MRData"]["ConstructorTable"]["Constructors"]
        # print(constructors)
        # apx = {
        #     "constructorId": "apx",
        #     "url": "https://en.wikipedia.org/wiki/Untitled_Joseph_Kosinski_film",
        #     "name": "APXGP",
        #     "nationality": "American"
        # }
        # constructors.append(apx)
        with open(f'constructors/{season}.json', 'w', encoding='utf-8') as file:
            json.dump(constructors, file, ensure_ascii=False, indent=4)
        print("Constructors updated successfully!")
    else:
        print(response.status_code)

def update_constructor_drivers():
    season = current_year

    with open(f'constructors/{current_year}.json', 'r', encoding='utf-8') as file:
        data = json.load(file)

    def fetchDrivers(team):
        print(team)
        response = api_get(f'{api_url}/{season}/constructors/{team}/drivers.json')
        if response.status_code == 200:
            responsedata = response.json()
            drivers = responsedata["MRData"]["DriverTable"]["Drivers"]
            # Ensure the directory exists
            folder_path = f'constructors/{season}'
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
            # Write the file in the folder
            with open(f'{folder_path}/{team}.json', 'w', encoding='utf-8') as file:
                json.dump(drivers, file, ensure_ascii=False, indent=4)
        else:
            print(response.status_code)


    for team in data:
        if team["constructorId"]!="apx":
            fetchDrivers(team["constructorId"])

    print("Constructor drivers updated successfully!")

def _latest_stored_race(season=None):
    season = int(season or current_year)
    races = load_json(f'races/{season}/results.json', [])
    completed = [race for race in races if race.get('Results')]
    if not completed:
        return None
    return max(completed, key=lambda race: int(race.get('round') or 0))


def season_is_complete(season=None):
    """True when every calendar round has a stored results list."""
    season = int(season or current_year)
    calendar = load_json(f'races/{season}/raceDetails.json', [])
    results = load_json(f'races/{season}/results.json', [])
    calendar_rounds = {str(race.get('round')) for race in calendar}
    completed = {
        str(race.get('round'))
        for race in results
        if race.get('Results')
    }
    return bool(calendar_rounds) and calendar_rounds <= completed


def update_driverData():
    from drivers_build import did_not_finish

    input_directory = 'drivers/'
    output_directory = 'drivers2/'
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    race = _latest_stored_race()
    if race is None:
        race = api_races(f'{api_url}/current/last/results.json')[0]
    raceName = race["raceName"]
    season = str(race["season"])
    round = str(race["round"])
    results = race["Results"]
    drivers_done = 0

    local_standings = load_json(f'races/{season}/driverStandings.json', {})
    standings_rows = local_standings.get(round) or local_standings.get(str(int(round))) or []
    if standings_rows:
        standings_by_driver = {
            standing['Driver']['driverId']: standing
            for standing in standings_rows
        }
    else:
        standings_url = f'{api_url}/{season}/{round}/driverStandings.json'
        standings_data = api_get_json(standings_url)
        standings_lists = (
            standings_data.get('MRData', {})
            .get('StandingsTable', {})
            .get('StandingsLists', [])
        )
        if not standings_lists:
            raise RuntimeError(f'API returned no driver standings: {standings_url}')
        standings_by_driver = {
            standing['Driver']['driverId']: standing
            for standing in standings_lists[0].get('DriverStandings', [])
        }

    qualifying_races = {
        str(item.get('round')): item
        for item in load_json(f'races/{season}/qualifying.json', [])
    }
    qualifying_race = qualifying_races.get(round)
    if qualifying_race is None:
        qualifying_url = f'{api_url}/{season}/{round}/qualifying.json'
        qualifying_race = api_races(qualifying_url)[0]
    qualifying_by_driver = {
        qualifying['Driver']['driverId']: qualifying
        for qualifying in (qualifying_race or {}).get('QualifyingResults', [])
    }
    sprint_races = {
        str(item.get('round')): item
        for item in load_json(f'races/{season}/sprint.json', [])
    }
    sprint_by_driver = {
        row['Driver']['driverId']: row
        for row in (sprint_races.get(round) or {}).get('SprintResults', [])
        if row.get('Driver', {}).get('driverId')
    }
    finished_weekend = set()

    for result in results:
            driverId = result["Driver"]["driverId"]
            input_file = os.path.join(input_directory, f'{driverId}.json')
            output_file = os.path.join(output_directory, f'{driverId}.json')

            # Load driver data from the input file or create a new default structure if it doesn't exist
            if os.path.exists(input_file):
                with open(input_file, 'r', encoding='utf-8') as file:
                    data = json.load(file)
            else:
                # Create a new file structure with default (empty) values
                data = {
                    "driverId": driverId,
                    "driverCode": result["Driver"].get("code", ""),
                    "driverNumber": result["Driver"].get("permanentNumber", ""),
                    "lastUpdate": "",
                    "totalWins": 0,
                    "totalPodiums": 0,
                    "totalPoles": 0,
                    "totalDNFs": 0,
                    "seasonWins": {},
                    "seasonPodiums": {},
                    "seasonPoles": {},
                    "seasonDNFs": {},
                    "poles": {},
                    "podiums": {},
                    "DNFs": {},
                    "fastLaps": {},
                    "finalStandings": {},
                    "posAfterRace": {},
                    "racePosition": {},
                    "qualiPosition": {},
                    "driverQualifyingTimes": {},
                    "consistency": {},
                    "peakSeason": {},
                    "avgRacePositions": {},
                    "avgQualiPositions": {},
                    "rates": {},
                    "winRate": 0.0,
                    "podiumRate": 0.0,
                    "poleRate": 0.0,
                    "dnfRate": 0.0,
                    "ptwConRate": {},
                    "positionsGainLost": {}
                }

            # Update last update time
            data["lastUpdate"] = datetime.datetime.now().isoformat()

            # Ensure keys exist for the new season
            if season not in data["seasonWins"]:
                data["seasonWins"][season] = 0
            if season not in data["seasonPodiums"]:
                data["seasonPodiums"][season] = 0
            if season not in data["seasonPoles"]:
                data["seasonPoles"][season] = 0
            if season not in data["seasonDNFs"]:
                data["seasonDNFs"][season] = 0
            if season not in data["fastLaps"]:
                data["fastLaps"][season] = {}
            if season not in data["DNFs"]:
                data["DNFs"][season] = {}
            if season not in data["podiums"]:
                data["podiums"][season] = {}
            if season not in data["racePosition"]:
                data["racePosition"][season] = {"year": season, "positions": {}}
            if season not in data["qualiPosition"]:
                data["qualiPosition"][season] = {"year": season, "positions": {}}
            if season not in data["driverQualifyingTimes"]:
                data["driverQualifyingTimes"][season] = {"year": season, "QualiTimes": {}}
            if season not in data["finalStandings"]:
                data["finalStandings"][season] = {"year": season, "position": "0", "points": "0"}
            if season not in data["posAfterRace"]:
                data["posAfterRace"][season] = {"year": season, "pos": {}}
            if season not in data["poles"]:
                data["poles"][season] = []

            data.setdefault("raceResults", {})
            data.setdefault("sprintResults", {})
            data["raceResults"].setdefault(season, {})
            data["sprintResults"].setdefault(season, {})
            existing_round = data["raceResults"][season].get(round)
            race_payload = {
                "round": int(round) if str(round).isdigit() else round,
                "raceName": raceName,
                "position": int(result["position"]) if str(result.get("position", "")).isdigit() else None,
                "points": parse_points(result.get("points")),
                "status": result.get("status"),
                "grid": result.get("grid"),
            }
            if existing_round is None or (
                (race_payload["points"] or 0) > (existing_round.get("points") or 0)
                or (
                    race_payload["position"] is not None
                    and (
                        existing_round.get("position") is None
                        or race_payload["position"] < existing_round["position"]
                    )
                )
            ):
                data["raceResults"][season][round] = race_payload
            sprint_row = sprint_by_driver.get(driverId)
            if sprint_row:
                data["sprintResults"][season][round] = {
                    "position": int(sprint_row["position"]) if str(sprint_row.get("position", "")).isdigit() else None,
                    "points": parse_points(sprint_row.get("points")),
                    "status": sprint_row.get("status"),
                }

            # Classified retirements still count as a DNF; +N laps do not.
            if did_not_finish(result.get("status")):
                if driverId not in finished_weekend:
                    data["DNFs"][season][raceName] = result["status"]
            else:
                finished_weekend.add(driverId)
                data["DNFs"][season].pop(raceName, None)
            data["seasonDNFs"][season] = len(data["DNFs"][season].keys())
            data["totalDNFs"] = sum(data["seasonDNFs"].values())

            if result["position"] in ["1", "2", "3"]:
                data["podiums"][season][raceName] = result["position"]
                data["seasonPodiums"][season] = len(data["podiums"][season].keys())
                data["totalPodiums"] = sum(data["seasonPodiums"].values())

            if "FastestLap" in result:
                data["fastLaps"][season][raceName] = result["FastestLap"]["Time"]["time"]
            else:
                data["fastLaps"][season][raceName] = -1

            data["racePosition"][season]["positions"][raceName] = result["position"]

            if result["position"] == "1":
                data["seasonWins"][season] = list(data["racePosition"][season]["positions"].values()).count("1")
                data["totalWins"] = sum(data["seasonWins"].values())

            data["qualiPosition"][season]["positions"][raceName] = result["grid"]

            # Reuse the single standings response fetched before the driver loop.
            driverStanding = standings_by_driver.get(driverId)
            if driverStanding:
                data["finalStandings"][season]["position"] = driverStanding.get("position", "40")
                data["finalStandings"][season]["points"] = driverStanding["points"]
                data["posAfterRace"][season]["pos"][raceName] = {"points": parse_points(driverStanding["points"])}
            else:
                raise RuntimeError(
                    f'Driver standings missing {driverId} for round {round}'
                )

            # Reuse the single qualifying response fetched before the driver loop.
            qualifying = qualifying_by_driver.get(driverId)
            if qualifying:
                val1 = qualifying.get("Q1", "N/A")
                val2 = qualifying.get("Q2", "N/A")
                val3 = qualifying.get("Q3", "N/A")
                data["driverQualifyingTimes"][season]["QualiTimes"][raceName] = [val1, val2, val3]

            # Save updated data to output file
            with open(output_file, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            drivers_done += 1
            print(drivers_done, output_file)
            print("------------------------------")
    print("Driver Data updated successfully!")

def analyse_driverData():
    input_directory = 'drivers2/'
    output_directory = 'drivers2/'

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    json_files = [f for f in os.listdir(input_directory) if f.endswith('.json')]

    def calculate_consistency(metric):
        mean = np.mean(metric)
        std_dev = np.std(metric)
        cv = std_dev / mean if mean !=0 else 0
        return mean, std_dev, cv

    def find_peak_season(metric, seasons):
        peak_value = np.max(metric)
        peak_season = seasons[np.argmax(metric)]
        if peak_value == 0:
            return "No peak season", 0
        return peak_season, peak_value

    def calculate_positions_gained_lost(seasons, race_positions, quali_positions):
        positions_gained_lost = {}
        for season in seasons:
            positions_gained_lost[season] = {}
            for race in race_positions[season]['positions']:
                if race in quali_positions[season]['positions']:
                    race_pos = race_positions[season]['positions'][race]
                    quali_pos = quali_positions[season]['positions'][race]
                    positions_gained_lost[season][race] = int(quali_pos) - int(race_pos)
        return positions_gained_lost

    def average_positions_gained_lost(seasons, positions_gained_lost):
        avg_positions_gained_lost = {}
        for season in seasons:
            gains_losses = list(positions_gained_lost[season].values())
            avg_positions_gained_lost[season] = np.mean(gains_losses) if gains_losses else 0
        return avg_positions_gained_lost

    def replace_nan_with_minus_one(d):
        def replace_nan(x):
            if isinstance(x, dict):
                return {k: replace_nan(v) for k, v in x.items()}
            elif isinstance(x, list):
                return [replace_nan(i) for i in x]
            elif isinstance(x, float) and math.isnan(x):
                return -1
            else:
                return x

        new_d = replace_nan(d)
        
        new_d = {(k if not (isinstance(k, float) and math.isnan(k)) else -1): v for k, v in new_d.items()}
        
        return new_d

    def convert_np_int_to_int(d):
        for key, value in d.items():
            if isinstance(value, np.int32):
                d[key] = int(value)
            elif isinstance(value, dict):
                convert_np_int_to_int(value)

    def process(input_file):
        with open(input_file, 'r', encoding='utf-8') as file:
            data = json.load(file)
        if(data):
            seasons = sorted(data['seasonWins'].keys())
            wins_per_season = [data['seasonWins'][season] for season in seasons]
            podiums_per_season = [data['seasonPodiums'][season] for season in seasons]
            poles_per_season = [data['seasonPoles'][season] for season in seasons]
            dnfs_per_season = [data['seasonDNFs'][season] for season in seasons]

            # final_positions = [int(data['finalStandings'][season]['position']) for season in seasons]
            points_per_season = [float(data['finalStandings'][season]['points']) for season in seasons]

            mean_wins, std_dev_wins, cv_wins = calculate_consistency(wins_per_season)
            mean_podiums, std_dev_podiums, cv_podiums = calculate_consistency(podiums_per_season)
            mean_poles, std_dev_poles, cv_poles = calculate_consistency(poles_per_season)
            mean_points, std_dev_points, cv_points = calculate_consistency(points_per_season)

            peak_season_wins, peak_wins = find_peak_season(wins_per_season, seasons)
            peak_season_podiums, peak_podiums = find_peak_season(podiums_per_season, seasons)
            peak_season_poles, peak_poles = find_peak_season(poles_per_season, seasons)

            race_positions_per_season = {season: [int(data['racePosition'][season]['positions'][race]) for race in data['racePosition'][season]['positions']] for season in seasons}
            quali_positions_per_season = {season: [int(data['qualiPosition'][season]['positions'][race]) for race in data['qualiPosition'][season]['positions']] for season in seasons}
            avg_race_positions = [np.mean(race_positions_per_season[season]) for season in seasons]
            avg_quali_positions = [np.mean(quali_positions_per_season[season]) for season in seasons]

            total_races_per_season = {season: len(data['racePosition'][season]['positions']) for season in seasons}
            total_races = 0
            pole_conversion_rate = {}
            for season in seasons:
                total_races += total_races_per_season[season]
                pole_races = data["poles"][season]
                if len(pole_races):
                    tempwins = 0
                    for racex in pole_races:
                        if data["racePosition"][season]["positions"][racex] == "1":
                            tempwins += 1
                    pole_conversion_rate[season] = tempwins/len(pole_races)
                else:
                    pole_conversion_rate[season] = -1
            win_rate_per_season = [wins_per_season[seasons.index(season)] / total_races_per_season[season] for season in seasons]
            podium_rate_per_season = [podiums_per_season[seasons.index(season)] / total_races_per_season[season] for season in seasons]
            pole_rate_per_season = [poles_per_season[seasons.index(season)] / total_races_per_season[season] for season in seasons]
            dnf_rate_per_season = [dnfs_per_season[seasons.index(season)] / total_races_per_season[season] for season in seasons]
            win_rate = data['totalWins']/total_races
            podium_rate = data['totalPodiums']/total_races
            pole_rate = data['totalPoles']/total_races
            dnf_rate = data['totalDNFs']/total_races


            # pole_conversion_rate = data['totalWins'] / data['totalPoles'] if data['totalPoles'] > 0 else 0
            positions_gained_lost = calculate_positions_gained_lost(seasons, data['racePosition'], data['qualiPosition'])

            avg_positions_gained_lost_per_season = average_positions_gained_lost(seasons, positions_gained_lost)

            data['consistency'] = {
                'mean' : {
                    'wins' : mean_wins,
                    'podiums' : mean_podiums,
                    'poles' : mean_poles,
                    'points' : mean_points
                },
                'std' : {
                    'wins' : std_dev_wins,
                    'podiums' : std_dev_podiums,
                    'poles' : std_dev_poles,
                    'points': std_dev_points
                },
                'cv' : {
                    'wins' : cv_wins,
                    'podiums' : cv_podiums,
                    'poles' : cv_poles,
                    'points' : cv_points
                }
            }
            data['peakSeason'] = {
                'wins' : {
                    'season' : peak_season_wins,
                    'wins' : peak_wins
                },
                'podiums' : {
                    'season': peak_season_podiums,
                    'podiums': peak_podiums
                },
                'poles' : {
                    'season' : peak_season_poles,
                    'poles' : peak_poles
                }
            }
            data['avgRacePositions'] = {
                season : avg_race_positions[seasons.index(season)]
                for season in seasons
            }
            data['avgQualiPositions'] = {
                season : avg_quali_positions[seasons.index(season)]
                for season in seasons
            }
            data['rates'] = {
                'wins' : {
                    season : win_rate_per_season[seasons.index(season)] for season in seasons
                },
                'podiums' : {
                    season : podium_rate_per_season[seasons.index(season)] for season in seasons
                },
                'poles' : {
                    season : pole_rate_per_season[seasons.index(season)] for season in seasons
                },
                'DNFs': {
                    season : dnf_rate_per_season[seasons.index(season)] for season in seasons
                }
            }
            data['winRate'] = win_rate
            data['podiumRate'] = podium_rate
            data['poleRate'] = pole_rate
            data['dnfRate'] = dnf_rate
            data['ptwConRate'] = pole_conversion_rate
            data['positionsGainLost'] = positions_gained_lost
            convert_np_int_to_int(data)
            # replace_nan_with_minus_one(data)
        return data


    files_done = 0
    for filename in json_files:
        input_file = os.path.join(input_directory, filename)
        output_file = os.path.join(output_directory, filename)
        files_done += 1
        print("Current file: ", input_file)

        processed_data = process(input_file)

        f = open(output_file, "w", encoding='utf-8')
        json.dump(processed_data, f, indent=4, ensure_ascii=False, cls=NpEncoder)
        f.close()
        print(files_done, input_file, output_file)
        print("---------------------------------------------")

    print("Driver Data analysis complete!")

def replace_NaN():
    input_directory = 'drivers2/'
    output_directory = 'drivers/'

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    json_files = [f for f in os.listdir(input_directory) if f.endswith('.json')]

    def replace_nan_with_minus_one(d):
        def replace_nan(x):
            if isinstance(x, dict):
                return {k: replace_nan(v) for k, v in x.items()}
            elif isinstance(x, list):
                return [replace_nan(i) for i in x]
            elif isinstance(x, float) and math.isnan(x):
                return -1
            else:
                return x

        new_d = replace_nan(d)
        
        new_d = {(k if not (isinstance(k, float) and math.isnan(k)) else -1): v for k, v in new_d.items()}
        
        return new_d

    files_done = 0
    for filename in json_files:
        input_file = os.path.join(input_directory, filename)
        output_file = os.path.join(output_directory, filename)
        files_done += 1
        print("Current file: ", input_file)

        with open(input_file, 'r', encoding='utf-8') as file:
            data = json.load(file)
        if(data):
            updated_data = replace_nan_with_minus_one(data)
        f = open(output_file, "w", encoding='utf-8')
        json.dump(updated_data, f, indent=4, ensure_ascii=False, cls=NpEncoder)
        f.close()
        print(files_done, input_file, output_file)
        print("-----------------------------------")
        
    shutil.rmtree('drivers2')
    print("Replaced NaNs in Driver Data!")

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

def ensure_directory_exists(directory_path):
    """Create directory if it doesn't exist"""
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Created directory: {directory_path}")

def ensure_file_exists(file_path, default_content):
    """Create file with default content if it doesn't exist"""
    directory = os.path.dirname(file_path)
    ensure_directory_exists(directory)
    
    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(default_content, file, indent=4, ensure_ascii=False)
        print(f"Created file: {file_path}")

def update_races():
    season = current_year
    
    # Ensure races directory exists
    ensure_directory_exists('races')
    
    # Ensure races.json exists with default structure
    races_file = 'races/races.json'
    default_races = {str(season): {}}
    ensure_file_exists(races_file, default_races)
    
    # Ensure racesbyMK.json exists with default structure
    races_by_mk_file = 'races/racesbyMK.json'
    ensure_file_exists(races_by_mk_file, {})
    
    response = api_get(f'https://api.openf1.org/v1/meetings?year={season}')
    if response.status_code == 200:
        responsedata = response.json()
        
        if not responsedata or len(responsedata) == 0:
            print("Warning: No race meetings found from OpenF1 API")
            return
            
        with open(races_file, 'r', encoding='utf-8') as file:
            data = json.load(file)
        
        # Create season entry if it doesn't exist
        if str(season) not in data:
            data[str(season)] = {}
        
        # Add all races to the data structure, not just the last one
        updated = False
        for race in responsedata:
            # Skip "Pre-Season Testing" events
            if "Pre-Season Testing" in race.get("meeting_name", ""):
                continue
                
            if race["meeting_name"] not in data[str(season)]:
                data[str(season)][race["meeting_name"]] = {}
                data[str(season)][race["meeting_name"]]["meeting_key"] = race["meeting_key"]
                data[str(season)][race["meeting_name"]]["location"] = race["location"]
                updated = True
                
                # Also update racesbyMK.json
                with open(races_by_mk_file, 'r', encoding='utf-8') as g:
                    result = json.load(g)
                
                result[race["meeting_key"]] = {}
                result[race["meeting_key"]]["raceName"] = race["meeting_name"]
                result[race["meeting_key"]]["location"] = race["location"]
                result[race["meeting_key"]]["year"] = str(season)
                
                with open(races_by_mk_file, 'w', encoding='utf-8') as g:
                    json.dump(result, g, indent=4, ensure_ascii=False, cls=NpEncoder)
        
        if updated:
            with open(races_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False, cls=NpEncoder)
            print(f"Added new races to races.json and racesbyMK.json")
        else:
            print("No new races to add")
    else:
        print(f"Failed to get race meetings from OpenF1 API (Status: {response.status_code})")
    
    print("Race Details updated successfully!")

def completed_calendar_races(races, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    completed = []
    for race in races:
        if race.get('time'):
            start = datetime.datetime.fromisoformat(f"{race['date']}T{race['time']}".replace('Z', '+00:00'))
            available = start + datetime.timedelta(hours=4)
        else:
            available = datetime.datetime.fromisoformat(race['date']).replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(days=1)
        if available <= now:
            completed.append(race)
    return completed


def update_round_records(file_name, endpoint_name, label, only_if=None):
    """Fetch only missing rounds and never replace an existing round."""
    season = current_year
    season_dir = f'races/{season}'
    ensure_directory_exists(season_dir)

    race_details_file = f'{season_dir}/raceDetails.json'
    ensure_file_exists(race_details_file, [])
    races = load_json(race_details_file, [])
    completed_races = completed_calendar_races(races)

    output_file = f'{season_dir}/{file_name}'
    existing = load_json(output_file, [])
    if not isinstance(existing, list):
        raise RuntimeError(f'Expected a JSON list in {output_file}')

    existing_rounds = {
        str(record.get('round'))
        for record in existing
        if record.get('round') is not None
    }
    additions = []
    considered = [
        race for race in completed_races
        if only_if is None or only_if(race)
    ]

    for race in considered:
        round_number = str(race['round'])
        if round_number in existing_rounds:
            print(f'{label}: round {round_number} already stored; preserving it')
            continue

        print(f'{label}: fetching {race["raceName"]} ({season})')
        url = f'{api_url}/{season}/{round_number}/{endpoint_name}.json'
        fetched_race = api_races(url)[0]
        fetched_race.pop('_mrdataTotal', None)
        if str(fetched_race.get('round')) != round_number:
            raise RuntimeError(
                f'API round mismatch for {race["raceName"]}: {url}'
            )
        result_key = ROUND_RESULT_KEYS.get(endpoint_name, 'Results')
        if not fetched_race.get(result_key):
            raise RuntimeError(f'API returned no {label.lower()}: {url}')
        additions.append(fetched_race)

    merged = merge_round_records(existing, additions)
    expected_rounds = {str(race['round']) for race in considered}
    merged_rounds = {str(record.get('round')) for record in merged}
    missing_rounds = sorted(expected_rounds - merged_rounds, key=int)
    if missing_rounds:
        raise RuntimeError(
            f'{label} validation failed; missing rounds: '
            f'{", ".join(missing_rounds)}'
        )

    if merged != existing:
        write_json_atomic(output_file, merged)
        print(f'{label}: added {len(additions)} new round(s)')
    else:
        print(f'{label}: no new rounds to add')


def update_raceResults():
    update_round_records('results.json', 'results', 'Race results')
    print("Race Results updated successfully!")


def update_qualifying():
    update_round_records('qualifying.json', 'qualifying', 'Qualifying')
    print("Qualifying results updated successfully!")


def update_sprintResults():
    update_round_records(
        'sprint.json',
        'sprint',
        'Sprint results',
        only_if=lambda race: bool(race.get('Sprint')),
    )
    print("Sprint results updated successfully!")


def update_standings(file_name, endpoint_name, standings_key, label):
    """Add missing per-round standings while preserving stored history."""
    season = current_year
    season_dir = f'races/{season}'
    ensure_directory_exists(season_dir)

    race_details_file = f'{season_dir}/raceDetails.json'
    ensure_file_exists(race_details_file, [])
    races = load_json(race_details_file, [])
    completed_races = completed_calendar_races(races)

    output_file = f'{season_dir}/{file_name}'
    existing = load_json(output_file, {})
    if not isinstance(existing, dict):
        raise RuntimeError(f'Expected a JSON object in {output_file}')
    result = dict(existing)

    for race in completed_races:
        round_number = str(race['round'])
        if round_number in result:
            print(f'{label}: round {round_number} already stored; preserving it')
            continue

        print(f'{label}: fetching {race["raceName"]} ({season})')
        url = f'{api_url}/{season}/{round_number}/{endpoint_name}.json'
        data = api_get_json(url)
        standings_lists = (
            data.get('MRData', {})
            .get('StandingsTable', {})
            .get('StandingsLists', [])
        )
        if not standings_lists or str(standings_lists[0].get('round')) != round_number or not standings_lists[0].get(standings_key):
            raise RuntimeError(
                f'API returned no {label.lower()} for '
                f'{race["raceName"]}: {url}'
            )
        result[round_number] = standings_lists[0][standings_key]

    expected_rounds = {str(race['round']) for race in completed_races}
    missing_rounds = sorted(expected_rounds - result.keys(), key=int)
    if missing_rounds:
        raise RuntimeError(
            f'{label} validation failed; missing rounds: '
            f'{", ".join(missing_rounds)}'
        )

    if completed_races:
        latest_round = max(expected_rounds, key=int)
        result['latest'] = result[latest_round]

    if result != existing:
        write_json_atomic(output_file, result)
        print(f'{label}: stored new standings')
    else:
        print(f'{label}: no new rounds to add')


def update_driverStandings():
    update_standings(
        'driverStandings.json',
        'driverStandings',
        'DriverStandings',
        'Driver standings',
    )
    print('Driver Standings updated successfully!')


def update_constructorStandings():
    update_standings(
        'constructorStandings.json',
        'constructorStandings',
        'ConstructorStandings',
        'Constructor standings',
    )
    print('Constructor Standings updated successfully!')
# This function is no longer needed as its functionality is included in update_all()
def initialize_race_details():
    """Initialize race details file for current season if it doesn't exist"""
    season = current_year
    season_dir = f'races/{season}'
    ensure_directory_exists(season_dir)
    
    race_details_file = f'{season_dir}/raceDetails.json'
    
    # If file doesn't exist or is empty, fetch race calendar from API
    if not os.path.exists(race_details_file) or os.path.getsize(race_details_file) == 0:
        races = fetch_race_calendar(season)
        if races and len(races) > 0:
            with open(race_details_file, 'w', encoding='utf-8') as file:
                json.dump(races, file, indent=4, ensure_ascii=False, cls=NpEncoder)
            print(f"Initialized race details for {season} with {len(races)} races")
        else:
            # Create empty array if API doesn't return expected data
            ensure_file_exists(race_details_file, [])
            print("Warning: Could not initialize race details from API")

def fetch_race_calendar(season):
    """Fetch race calendar from API for given season, excluding Pre-Season Testing"""
    url = f'{api_url}/{season}.json'
    try:
        responsedata = api_get_json(url)
    except RuntimeError as exc:
        print(f"Warning: Couldn't fetch race calendar for {season} from API: {exc}")
        return []

    races = (
        responsedata.get('MRData', {})
        .get('RaceTable', {})
        .get('Races', [])
    )
    return [
        race for race in races
        if 'Pre-Season Testing' not in race.get('raceName', '')
    ]

def pre_checks():
    """Perform pre-update checks and initialize necessary files"""
    season = current_year
    
    # Ensure base directory structure exists
    ensure_directory_exists('races')
    ensure_directory_exists(f'races/{season}')
    
    # Initialize races.json with proper structure if empty
    races_file = 'races/races.json'
    if not os.path.exists(races_file) or os.path.getsize(races_file) == 0:
        with open(races_file, 'w', encoding='utf-8') as f:
            json.dump({str(season): {}}, f, indent=4, ensure_ascii=False, cls=NpEncoder)
        print(f"Initialized races.json with structure for {season}")
    
    # Initialize racesbyMK.json if empty
    races_by_mk_file = 'races/racesbyMK.json'
    if not os.path.exists(races_by_mk_file) or os.path.getsize(races_by_mk_file) == 0:
        with open(races_by_mk_file, 'w', encoding='utf-8') as f:
            json.dump({}, f, indent=4, ensure_ascii=False, cls=NpEncoder)
        print(f"Initialized racesbyMK.json with empty structure")
    
    # Initialize race details which other functions depend on
    race_details_file = f'races/{season}/raceDetails.json'
    if not os.path.exists(race_details_file) or os.path.getsize(race_details_file) == 0:
        races = fetch_race_calendar(season)
        with open(race_details_file, 'w', encoding='utf-8') as f:
            json.dump(races, f, indent=4, ensure_ascii=False, cls=NpEncoder)
        print(f"Initialized raceDetails.json with {len(races)} races for {season}")
    
    # Verify race details file has data before proceeding
    with open(race_details_file, 'r', encoding='utf-8') as f:
        race_data = json.load(f)
    
    if not race_data or len(race_data) == 0:
        print("Warning: No race data found for the current season. Attempting to fetch from API...")
        race_data = fetch_race_calendar(season)
        if race_data and len(race_data) > 0:
            with open(race_details_file, 'w', encoding='utf-8') as f:
                json.dump(race_data, f, indent=4, ensure_ascii=False, cls=NpEncoder)
            print(f"Successfully fetched and saved {len(race_data)} races for {season}")
            return True
        else:
            print("Error: Could not fetch race data from API. Manual initialization required.")
            print("Skipping remaining updates as they depend on race data.")
            return False
    
    return True

LINEAGE_FILE = 'teamLineage.json'
HISTORICAL_POLES_FILE = 'historicalPoles.json'
TEAM_RECORDS_FILE = 'teamRecords.json'
BACKFILL_PROGRESS_FILE = 'backfillProgress.json'


def _final_standings(season, kind):
    """Return the end-of-season standings list for a season, or []."""
    data = load_json(f'races/{season}/{kind}.json', {})
    if not isinstance(data, dict) or not data:
        return []
    if 'latest' in data:
        return data['latest']
    rounds = [k for k in data if k.isdigit()]
    if not rounds:
        return []
    return data[str(max(int(k) for k in rounds))]


def _season_complete(season, results):
    """A season only counts toward titles once every scheduled race has results.
    This holds for finished historical seasons and keeps an in-progress season --
    or one left partially fetched, since the updater only refreshes current_year --
    from having its current standings leader recorded as champion."""
    scheduled = len(load_json(f'races/{season}/raceDetails.json', []))
    completed = sum(1 for race in results if race.get('Results'))
    return scheduled > 0 and completed >= scheduled


def _season_records_index():
    """Aggregate every stored season once: per-constructor wins, podiums, fastest
    laps, poles and driver line-ups, plus that season's champions."""
    from poles import is_sprint_weekend, pole_sitter
    from scoring import is_classified, parse_points

    index = {}
    for entry in sorted(os.listdir('races')):
        if not entry.isdigit():
            continue
        season = int(entry)
        results = load_json(f'races/{season}/results.json', [])
        qualifying = {
            str(race.get('round')): race
            for race in load_json(f'races/{season}/qualifying.json', [])
        }
        sprints = {
            str(race.get('round')): race
            for race in load_json(f'races/{season}/sprint.json', [])
        }
        details = {
            str(race.get('round')): race
            for race in load_json(f'races/{season}/raceDetails.json', [])
        }
        wins, podiums, fastest, poles = {}, {}, {}, {}
        lineup = {}
        driver_stats = {}

        def stats_for(constructor_id, driver_id, name):
            constructor_stats = driver_stats.setdefault(constructor_id, {})
            row = constructor_stats.get(driver_id)
            if row is None:
                row = {
                    'driverId': driver_id,
                    'name': name,
                    'starts': 0,
                    'wins': 0,
                    'podiums': 0,
                    'poles': 0,
                    'frontRowStarts': 0,
                    'pointsFinishes': 0,
                    'points': 0.0,
                    'positionCounts': {},
                    'fastestLaps': 0,
                    'fastestLapsTop10': 0,
                    'sprintPositionCounts': {},
                    'sharedCars': 0,
                }
                constructor_stats[driver_id] = row
            return row

        for race in results:
            rows = race.get('Results') or []
            position_counts = {}
            for result in rows:
                position_counts[str(result.get('position'))] = (
                    position_counts.get(str(result.get('position')), 0) + 1
                )
            official_pole = pole_sitter(
                season,
                race,
                qualifying.get(str(race.get('round'))),
                is_sprint_weekend(details.get(str(race.get('round')))),
            )
            if official_pole and official_pole.get('constructorId'):
                constructor_id = official_pole['constructorId']
                poles[constructor_id] = poles.get(constructor_id, 0) + 1
                if official_pole.get('driverId'):
                    stats_for(
                        constructor_id,
                        official_pole['driverId'],
                        official_pole.get('name') or official_pole['driverId'],
                    )['poles'] += 1
            for result in rows:
                constructor_id = result['Constructor']['constructorId']
                position = result.get('position')
                driver = result.get('Driver') or {}
                driver_id = driver.get('driverId')
                name = f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()
                if position == '1':
                    wins[constructor_id] = wins.get(constructor_id, 0) + 1
                if position in ('1', '2', '3'):
                    podiums[constructor_id] = podiums.get(constructor_id, 0) + 1
                if result.get('FastestLap', {}).get('rank') == '1':
                    fastest[constructor_id] = fastest.get(constructor_id, 0) + 1
                lineup.setdefault(constructor_id, {})
                if driver_id and driver_id not in lineup[constructor_id]:
                    lineup[constructor_id][driver_id] = {'name': name, 'starts': 0, 'points': 0.0}
                if driver_id:
                    lineup[constructor_id][driver_id]['starts'] += 1
                    lineup[constructor_id][driver_id]['points'] += parse_points(result.get('points'))
                    row = stats_for(constructor_id, driver_id, name)
                    row['starts'] += 1
                    if position == '1':
                        row['wins'] += 1
                    if position in ('1', '2', '3'):
                        row['podiums'] += 1
                    grid = str(result.get('grid'))
                    if grid in ('1', '2'):
                        row['frontRowStarts'] += 1
                    points = parse_points(result.get('points'))
                    row['points'] += points
                    if points > 0:
                        row['pointsFinishes'] += 1
                    if str(position).isdigit():
                        row['positionCounts'][str(position)] = (
                            row['positionCounts'].get(str(position), 0) + 1
                        )
                    if result.get('FastestLap', {}).get('rank') == '1':
                        row['fastestLaps'] += 1
                        classified_pos = int(result['positionText']) if is_classified(result) else None
                        if classified_pos is not None and classified_pos <= 10:
                            row['fastestLapsTop10'] += 1
                    if position_counts.get(str(position), 0) > 1:
                        row['sharedCars'] += 1

            sprint = sprints.get(str(race.get('round')))
            if sprint:
                for result in sprint.get('SprintResults') or []:
                    driver = result.get('Driver') or {}
                    driver_id = driver.get('driverId')
                    constructor_id = (result.get('Constructor') or {}).get('constructorId')
                    if not driver_id or not constructor_id:
                        continue
                    name = f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()
                    row = stats_for(constructor_id, driver_id, name)
                    sprint_pos = str(result.get('position'))
                    if sprint_pos.isdigit():
                        row['sprintPositionCounts'][sprint_pos] = (
                            row['sprintPositionCounts'].get(sprint_pos, 0) + 1
                        )

        constructor_standings = _final_standings(season, 'constructorStandings')
        constructor_champion = (
            constructor_standings[0]['Constructor']['constructorId']
            if constructor_standings else None
        )
        driver_standings = _final_standings(season, 'driverStandings')
        driver_champion = None
        standings_by_driver = {}
        if driver_standings:
            top = driver_standings[0]
            driver_champion = {
                'driverId': top['Driver'].get('driverId'),
                'name': f"{top['Driver'].get('givenName', '')} "
                        f"{top['Driver'].get('familyName', '')}".strip(),
                'cids': [c['constructorId'] for c in top.get('Constructors', [])],
            }
            for row in driver_standings:
                driver_id = (row.get('Driver') or {}).get('driverId')
                if driver_id:
                    standings_by_driver[driver_id] = {
                        'position': int(row.get('position') or 0),
                        'points': parse_points(row.get('points')),
                    }

        index[season] = {
            'wins': wins, 'podiums': podiums, 'fastest': fastest, 'poles': poles,
            'lineup': lineup, 'constructor_champion': constructor_champion,
            'driver_champion': driver_champion,
            'standings_by_driver': standings_by_driver,
            'driver_stats': driver_stats,
            'titles_final': _season_complete(season, results),
        }
    return index


LEADER_STATS = (
    'starts', 'wins', 'podiums', 'poles',
    'frontRowStarts', 'pointsFinishes', 'points',
)


def _merge_driver_stats(target, incoming):
    for driver_id, row in incoming.items():
        existing = target.get(driver_id)
        if existing is None:
            target[driver_id] = {
                key: (value.copy() if isinstance(value, dict) else value)
                for key, value in row.items()
            }
            continue
        for key in (
            'starts', 'wins', 'podiums', 'poles', 'frontRowStarts',
            'pointsFinishes', 'points', 'fastestLaps', 'fastestLapsTop10',
            'sharedCars',
        ):
            existing[key] += row.get(key, 0)
        for key in ('positionCounts', 'sprintPositionCounts'):
            for place, count in (row.get(key) or {}).items():
                existing[key][place] = existing[key].get(place, 0) + count


def _leaders_from_stats(stats):
    drivers = list(stats.values())
    leaders = {}
    for index, key in enumerate(LEADER_STATS):
        rest = LEADER_STATS[index + 1:]

        def sort_key(row, stat=key, remainder=rest):
            values = [-row.get(stat, 0)]
            for other in remainder:
                values.append(-row.get(other, 0))
            values.append(row.get('name') or row.get('driverId') or '')
            return tuple(values)

        ranked = sorted(drivers, key=sort_key)
        leaders[key] = [
            {
                'driverId': row['driverId'],
                'name': row['name'],
                'value': row.get(key, 0),
            }
            for row in ranked[:3]
        ]
    return leaders


def _position_counts_payload(stats):
    payload = {}
    for driver_id, row in stats.items():
        payload[driver_id] = {
            'name': row['name'],
            'positions': row['positionCounts'],
            'fastestLaps': row['fastestLaps'],
            'fastestLapsTop10': row['fastestLapsTop10'],
            'sprintPositions': row['sprintPositionCounts'],
            'sharedCars': row['sharedCars'],
        }
    return payload


def _blank_record():
    return {'wins': 0, 'podiums': 0, 'poles': 0, 'fastestLaps': 0,
            'constructorTitles': [], 'driverTitles': []}


def update_team_records():
    """Build teamRecords.json: per-era and combined career stats for the direct
    lineage of each current constructor, computed only from stored race data."""
    lineage = load_json(LINEAGE_FILE, {}).get('teams', {})
    if not lineage:
        print('No teamLineage.json found; skipping team records.')
        return
    index = _season_records_index()

    latest_season = max(index) if index else current_year
    latest_round = 0
    for race in load_json(f'races/{latest_season}/results.json', []):
        if race.get('Results'):
            latest_round = max(latest_round, int(race.get('round', 0)))

    teams = {}
    for team_id, config in lineage.items():
        eras, total = [], _blank_record()
        total_stats = {}
        for era in config['lineage']:
            constructor_ids = set(era['constructorIds'])
            start = era['startYear']
            end = era['endYear'] if era['endYear'] is not None else current_year
            record = _blank_record()
            record.update({
                'team': era['team'], 'constructorIds': era['constructorIds'],
                'startYear': start, 'endYear': end,
                'current': era['endYear'] is None,
            })
            era_stats = {}
            for season in range(start, end + 1):
                data = index.get(season)
                if not data:
                    continue
                for constructor_id in constructor_ids:
                    record['wins'] += data['wins'].get(constructor_id, 0)
                    record['podiums'] += data['podiums'].get(constructor_id, 0)
                    record['poles'] += data['poles'].get(constructor_id, 0)
                    record['fastestLaps'] += data['fastest'].get(constructor_id, 0)
                    _merge_driver_stats(
                        era_stats, data['driver_stats'].get(constructor_id, {})
                    )
                if data['titles_final'] and data['constructor_champion'] in constructor_ids:
                    lineup_rows = []
                    champion_id = (data['driver_champion'] or {}).get('driverId')
                    for constructor_id in constructor_ids:
                        for driver_id, info in data['lineup'].get(constructor_id, {}).items():
                            standing = data['standings_by_driver'].get(driver_id) or {}
                            lineup_rows.append({
                                'driverId': driver_id,
                                'name': info['name'],
                                'championshipPosition': standing.get('position'),
                                'points': standing.get('points', info.get('points', 0)),
                                'starts': info.get('starts', 0),
                                'isChampion': bool(
                                    champion_id == driver_id
                                    and any(
                                        cid in constructor_ids
                                        for cid in (data['driver_champion'] or {}).get('cids', [])
                                    )
                                ),
                            })
                    lineup_rows.sort(key=lambda item: (
                        0 if item['isChampion'] else 1,
                        -(item['points'] or 0),
                        -(item['starts'] or 0),
                        item['name'] or '',
                    ))
                    record['constructorTitles'].append({
                        'year': season,
                        'drivers': [item['name'] for item in lineup_rows],
                        'lineup': lineup_rows,
                    })
                if (data['titles_final'] and data['driver_champion']
                        and any(c in constructor_ids
                                for c in data['driver_champion']['cids'])):
                    record['driverTitles'].append({
                        'year': season,
                        'driver': data['driver_champion']['name'],
                        'driverId': data['driver_champion'].get('driverId'),
                    })
            record['leaders'] = _leaders_from_stats(era_stats)
            record['driverPositionCounts'] = _position_counts_payload(era_stats)
            _merge_driver_stats(total_stats, era_stats)
            for key in ('wins', 'podiums', 'poles', 'fastestLaps'):
                total[key] += record[key]
            total['constructorTitles'] += record['constructorTitles']
            total['driverTitles'] += record['driverTitles']
            eras.append(record)
        total['constructorTitles'].sort(key=lambda item: item['year'])
        total['driverTitles'].sort(key=lambda item: item['year'])
        total['leaders'] = _leaders_from_stats(total_stats)
        total['driverPositionCounts'] = _position_counts_payload(total_stats)
        teams[team_id] = {'name': config['name'], 'color': config['color'],
                          'total': total, 'lineage': eras}

    write_json_atomic(TEAM_RECORDS_FILE, {
        'generatedAt': dt.now().strftime('%Y-%m-%d'),
        'through': {'season': latest_season, 'round': latest_round},
        'source': 'f1nsight-api-2 local race data (Ergast/Jolpica lineage)',
        'notes': {
            'poles': 'Pole goes to the driver who started the Grand Prix from '
                     'grid 1. 2022 sprint weekends use Friday qualifying P1. '
                     'If nobody started from grid 1, qualifying P1 is used.',
            'fastestLaps': 'Fastest-lap data is only available from 2004.',
        },
        'teams': teams,
    })
    print('Team records updated successfully!')


def _backfill_key(kind, season, round_number):
    return f'{kind}:{season}:{round_number}'


def _backfill_done(kind, season, round_number):
    done = set(load_json(BACKFILL_PROGRESS_FILE, {}).get('done') or [])
    return _backfill_key(kind, season, round_number) in done


def _mark_backfill_done(kind, season, round_number):
    payload = load_json(BACKFILL_PROGRESS_FILE, {'done': []})
    key = _backfill_key(kind, season, round_number)
    if key not in payload['done']:
        payload['done'].append(key)
        write_json_atomic(BACKFILL_PROGRESS_FILE, payload)


def listed_seasons(only=None):
    if only:
        return sorted(int(season) for season in only)
    seasons = [
        int(entry) for entry in os.listdir('races')
        if entry.isdigit()
    ]
    return sorted(seasons)


def _season_rounds(season, completed_only=True):
    """Prefer the calendar; fall back to already-stored results."""
    details = load_json(f'races/{season}/raceDetails.json', [])
    if details:
        if completed_only:
            return completed_calendar_races(details)
        return details
    return load_json(f'races/{season}/results.json', [])


def _is_missing_endpoint(error):
    text = str(error)
    return 'status 404' in text or 'API returned no race data' in text


def _is_rate_limit(error):
    text = str(error)
    return 'status 429' in text or '429' in text


def _fetch_with_rate_limit_pause(fetcher, label, season, round_number):
    """Retry a single round forever on 429 so a long backfill can cool off."""
    while True:
        try:
            return fetcher()
        except RuntimeError as error:
            if _is_rate_limit(error):
                print(
                    f'{label}: rate-limited on {season} round {round_number}; '
                    f'sleeping 120s then retrying'
                )
                time.sleep(120)
                continue
            raise


def backfill_round_file(
    season,
    file_name,
    endpoint_name,
    label,
    *,
    refresh_count=30,
    fetch_missing=True,
    only_rounds=None,
    allow_missing=False,
):
    """Re-download truncated or missing rounds and write the season file atomically.

    A stored round is replaced only when the new fetch has at least as many rows.
    Each successful replacement writes the whole season document so a restart can
    skip rounds that are no longer truncated.
    """
    list_key = ROUND_RESULT_KEYS[endpoint_name]
    path = f'races/{season}/{file_name}'
    existing = load_json(path, [])
    if existing and not isinstance(existing, list):
        raise RuntimeError(f'Expected a JSON list in {path}')
    by_round = {
        str(record.get('round')): record
        for record in existing
        if record.get('round') is not None
    }
    rounds = _season_rounds(season)
    if only_rounds is not None:
        allowed = {str(round_number) for round_number in only_rounds}
        rounds = [race for race in rounds if str(race.get('round')) in allowed]

    wrote = 0
    gaps = []
    for race in rounds:
        round_number = str(race.get('round'))
        stored = by_round.get(round_number)
        stored_n = len(stored.get(list_key) or []) if stored else 0
        missing = stored is None
        truncated = stored_n == refresh_count
        if missing and not fetch_missing:
            continue
        if not missing and not truncated:
            continue
        if _backfill_done(endpoint_name, season, round_number):
            continue

        url = f'{api_url}/{season}/{round_number}/{endpoint_name}.json'
        print(f'{label}: fetching {season} round {round_number}')
        try:
            fetched = _fetch_with_rate_limit_pause(
                lambda: api_races(url)[0],
                label,
                season,
                round_number,
            )
        except RuntimeError as error:
            if allow_missing and _is_missing_endpoint(error):
                gaps.append({
                    'season': season,
                    'round': round_number,
                    'endpoint': endpoint_name,
                    'reason': str(error),
                })
                print(f'Gap: no {label.lower()} for {season} round {round_number}')
                _mark_backfill_done(endpoint_name, season, round_number)
                continue
            raise

        fetched_n = len(fetched.get(list_key) or [])
        fetched_total = fetched.pop('_mrdataTotal', fetched_n)
        complete = fetched_n >= fetched_total > 0
        if stored is not None and fetched_n < stored_n:
            if stored_n == refresh_count and complete:
                print(
                    f'{label}: replace mixed/truncated {season} round '
                    f'{round_number}; complete fetch {fetched_n} '
                    f'(MRData.total={fetched_total}) < stored {stored_n}'
                )
            else:
                print(
                    f'{label}: skip {season} round {round_number}; '
                    f'fetched {fetched_n} < stored {stored_n}'
                )
                continue
        fetched.pop('_mrdataTotal', None)
        by_round[round_number] = fetched
        merged = sorted(
            by_round.values(),
            key=lambda record: int(record.get('round', 10**9)),
        )
        write_json_atomic(path, merged)
        _mark_backfill_done(endpoint_name, season, round_number)
        wrote += 1
        print(
            f'{label}: {season} round {round_number} '
            f'{stored_n} -> {fetched_n}'
        )
    return wrote, gaps


def backfill_standings_file(
    season,
    file_name,
    endpoint_name,
    standings_key,
    label,
    *,
    refresh_count=30,
    fetch_missing=True,
):
    path = f'races/{season}/{file_name}'
    existing = load_json(path, {})
    if existing and not isinstance(existing, dict):
        raise RuntimeError(f'Expected a JSON object in {path}')
    result = {
        key: value for key, value in existing.items()
        if key != 'latest'
    }
    round_numbers = {key for key in result if key.isdigit()}
    round_numbers.update(
        str(race.get('round'))
        for race in _season_rounds(season)
        if race.get('round') is not None
    )

    wrote = 0
    gaps = []
    for round_number in sorted(round_numbers, key=int):
        stored = result.get(round_number)
        stored_n = len(stored) if isinstance(stored, list) else 0
        missing = stored is None
        truncated = stored_n == refresh_count
        if missing and not fetch_missing:
            continue
        if not missing and not truncated:
            continue
        if _backfill_done(endpoint_name, season, round_number):
            continue

        url = f'{api_url}/{season}/{round_number}/{endpoint_name}.json'
        print(f'{label}: fetching {season} round {round_number}')
        try:
            data = _fetch_with_rate_limit_pause(
                lambda: api_get_json(url),
                label,
                season,
                round_number,
            )
        except RuntimeError as error:
            if _is_missing_endpoint(error):
                gaps.append({
                    'season': season,
                    'round': round_number,
                    'endpoint': endpoint_name,
                    'reason': str(error),
                })
                print(f'Gap: no {label.lower()} for {season} round {round_number}')
                _mark_backfill_done(endpoint_name, season, round_number)
                continue
            raise
        standings_lists = (
            data.get('MRData', {})
            .get('StandingsTable', {})
            .get('StandingsLists', [])
        )
        fetched = (
            standings_lists[0].get(standings_key, [])
            if standings_lists else []
        )
        if not fetched:
            gaps.append({
                'season': season,
                'round': round_number,
                'endpoint': endpoint_name,
                'reason': 'empty standings list',
            })
            print(f'Gap: empty {label.lower()} for {season} round {round_number}')
            continue
        if stored is not None and len(fetched) < stored_n:
            try:
                fetched_total = int((data.get('MRData') or {}).get('total') or 0)
            except (TypeError, ValueError):
                fetched_total = len(fetched)
            complete = len(fetched) >= fetched_total > 0
            if stored_n == refresh_count and complete:
                print(
                    f'{label}: replace mixed/truncated {season} round '
                    f'{round_number}; complete fetch {len(fetched)} '
                    f'(MRData.total={fetched_total}) < stored {stored_n}'
                )
            else:
                print(
                    f'{label}: skip {season} round {round_number}; '
                    f'fetched {len(fetched)} < stored {stored_n}'
                )
                continue
        result[round_number] = fetched
        numeric_rounds = [key for key in result if key.isdigit()]
        if numeric_rounds:
            latest_round = max(numeric_rounds, key=int)
            result['latest'] = result[latest_round]
        write_json_atomic(path, result)
        _mark_backfill_done(endpoint_name, season, round_number)
        wrote += 1
        print(
            f'{label}: {season} round {round_number} '
            f'{stored_n} -> {len(fetched)}'
        )
    return wrote, gaps


def backfill_history(seasons=None, include_sprints=False):
    """Re-download truncated historical results, standings and qualifying.

    Resumable: rounds that no longer have exactly 30 rows are skipped.
    Gaps (404 or empty payloads) are printed and stored in backfillGaps.json.
    """
    targets = listed_seasons(seasons)
    all_gaps = load_json('backfillGaps.json', [])
    if not isinstance(all_gaps, list):
        all_gaps = []

    def record_gaps(gaps):
        if not gaps:
            return
        all_gaps.extend(gaps)
        write_json_atomic('backfillGaps.json', all_gaps)

    print('==========Backfilling race results==========')
    for season in targets:
        wrote, gaps = backfill_round_file(
            season, 'results.json', 'results', 'Race results',
            fetch_missing=False,
        )
        record_gaps(gaps)
        if wrote:
            print(f'Race results {season}: replaced {wrote} round(s)')

    print('==========Backfilling driver standings==========')
    for season in targets:
        wrote, gaps = backfill_standings_file(
            season,
            'driverStandings.json',
            'driverStandings',
            'DriverStandings',
            'Driver standings',
        )
        record_gaps(gaps)
        if wrote:
            print(f'Driver standings {season}: replaced {wrote} round(s)')

    print('==========Backfilling constructor standings==========')
    for season in targets:
        if season < 1958:
            continue
        wrote, gaps = backfill_standings_file(
            season,
            'constructorStandings.json',
            'constructorStandings',
            'ConstructorStandings',
            'Constructor standings',
        )
        record_gaps(gaps)
        if wrote:
            print(f'Constructor standings {season}: replaced {wrote} round(s)')

    qualifying_from = 1994
    print(f'==========Backfilling qualifying ({qualifying_from}+)==========')
    for season in targets:
        if season < qualifying_from:
            continue
        wrote, gaps = backfill_round_file(
            season,
            'qualifying.json',
            'qualifying',
            'Qualifying',
            fetch_missing=True,
            allow_missing=True,
        )
        record_gaps(gaps)
        if wrote:
            print(f'Qualifying {season}: stored {wrote} round(s)')

    if include_sprints:
        print('==========Backfilling sprint results==========')
        for season in targets:
            if season < 2021:
                continue
            sprint_rounds = [
                str(race.get('round'))
                for race in load_json(f'races/{season}/raceDetails.json', [])
                if race.get('Sprint')
            ]
            if not sprint_rounds:
                continue
            wrote, gaps = backfill_round_file(
                season,
                'sprint.json',
                'sprint',
                'Sprint results',
                fetch_missing=True,
                only_rounds=sprint_rounds,
                allow_missing=True,
            )
            record_gaps(gaps)
            if wrote:
                print(f'Sprint results {season}: stored {wrote} round(s)')

    try:
        from scoring import apply_scoring_overrides
        apply_scoring_overrides(targets)
    except Exception as error:
        print(f'Override application deferred: {error}')

    print('Backfill complete.')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Update published F1 JSON data.')
    parser.add_argument(
        '--backfill-history',
        action='store_true',
        help='Re-download truncated historical results, standings and qualifying.',
    )
    parser.add_argument(
        '--include-sprints',
        action='store_true',
        help='Also fetch sprint results for 2021 onward during a backfill.',
    )
    parser.add_argument(
        '--validate-scoring',
        action='store_true',
        help='Print scoring validation mismatches and exit.',
    )
    parser.add_argument(
        '--season',
        type=int,
        action='append',
        help='Limit backfill or validation to one or more seasons.',
    )
    parser.add_argument(
        '--rebuild-drivers',
        action='store_true',
        help='Rebuild driver JSON files from stored race data.',
    )
    parser.add_argument(
        '--rescore',
        action='store_true',
        help='Write scoring/rescored standings for every system.',
    )
    parser.add_argument(
        '--team-records',
        action='store_true',
        help='Rebuild teamRecords.json only.',
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.backfill_history:
        backfill_history(
            seasons=args.season,
            include_sprints=args.include_sprints,
        )
        return
    if args.validate_scoring:
        from scoring import validate_scoring
        validate_scoring(seasons=args.season)
        return
    if args.rebuild_drivers:
        from drivers_build import rebuild_driver_files
        report = rebuild_driver_files(write=True)
        print(json.dumps({
            'written': report['written'],
            'explainedCount': len(report['explained']),
            'unexplainedCount': len(report['unexplained']),
            'poleDiffs': len(report['poleDiffs']),
            'driverFileCount': report['driverFileCount'],
            'driversListCount': report['driversListCount'],
            'filesNotInList': report['filesNotInList'],
            'listNotInFiles': report['listNotInFiles'],
        }, indent=2))
        if report['unexplained']:
            print('UNEXPLAINED shrinking lists/counts:')
            print(json.dumps(report['unexplained'][:50], indent=2, default=str))
            raise SystemExit(1)
        return
    if args.rescore:
        from scoring import apply_scoring_overrides, write_rescored_files
        apply_scoring_overrides(args.season)
        mismatches = write_rescored_files(args.season)
        documented = [row for row in mismatches if row.get('documented')]
        unexplained = [row for row in mismatches if not row.get('documented')]
        print(f'Rescore mismatches vs official: {len(mismatches)} '
              f'({len(documented)} documented, {len(unexplained)} unexplained)')
        for row in mismatches:
            print(row)
        if unexplained:
            raise SystemExit(1)
        return
    if args.team_records:
        update_team_records()
        return
    update()


def update():
    print("==========Updating constructors==========")
    update_constructors()
    print("==========Updating constructor drivers==========")
    update_constructor_drivers()
    print("==========Updating Race Details==========")
    if not pre_checks():
        raise RuntimeError('No race calendar available; skipping publication')
    try:
        update_races()
    except RuntimeError as error:
        print(f'OpenF1 meeting information deferred: {error}')
    print("==========Updating Race Results==========")
    update_raceResults()
    print("==========Updating Qualifying Sessions==========")
    update_qualifying()
    print("==========Updating Sprint Results==========")
    update_sprintResults()
    print("==========Updating Driver Standings==========")
    update_driverStandings()
    print("==========Updating Constructor Standings==========")
    update_constructorStandings()
    print("==========Updating Driver Data==========")
    update_driverData()
    print("==========Refreshing season poles==========")
    from drivers_build import refresh_season_poles, rebuild_drivers_list
    refresh_season_poles(current_year, directory='drivers2')
    print("==========Analysing Driver Data==========")
    analyse_driverData()
    print("==========Replacing NaNs in Driver Data==========")
    replace_NaN()
    refresh_season_poles(current_year, directory='drivers')
    print("==========Updating Team Records==========")
    update_team_records()
    print("==========Updating driversList.json==========")
    rebuild_drivers_list(write=True)
    print("==========Rescoring championships==========")
    from scoring import apply_scoring_overrides, write_rescored_files
    apply_scoring_overrides()
    mismatches = write_rescored_files()
    unexplained = [row for row in mismatches if not row.get('documented')]
    print(f'Rescore mismatches vs official: {len(mismatches)} '
          f'({len(mismatches) - len(unexplained)} documented, '
          f'{len(unexplained)} unexplained)')
    for row in unexplained:
        print(row)

if __name__ == '__main__':
    main()
