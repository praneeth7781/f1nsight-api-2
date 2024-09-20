import requests, json, os, datetime, math, numpy as np, shutil
from datetime import datetime as dt

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
    season = 2024
    response = requests.get(f'https://ergast.com/api/f1/{season}/constructors.json')
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
    season = 2024

    with open(f'constructors/{2024}.json', 'r', encoding='utf-8') as file:
        data = json.load(file)

    def fetchDrivers(team):
        print(team)
        response = requests.get(f'https://ergast.com/api/f1/{season}/constructors/{team}/drivers.json')
        if response.status_code==200:
            responsedata = response.json()
            drivers = responsedata["MRData"]["DriverTable"]["Drivers"]
            with open(f'constructors/{season}/{team}.json', 'w', encoding='utf-8') as file:
                json.dump(drivers, file, ensure_ascii=False, indent=4)
        else:
            print(response.status_code)

    for team in data:
        if team["constructorId"]!="apx":
            fetchDrivers(team["constructorId"])

    print("Constructor drivers updated successfully!")

def update_driverData():
    url1 = 'https://ergast.com/api/f1/current/last/results.json'

    response1=requests.get(url1)

    input_directory = 'drivers/'
    output_directory = 'drivers2/'
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    if response1.status_code == 200:
        responsedata1 = response1.json()
        race = responsedata1["MRData"]["RaceTable"]["Races"][0]
        raceName = race["raceName"]
        season = race["season"]
        round = race["round"]
        results = race["Results"]
        drivers_done = 0
        for result in results:
            driverId = result["Driver"]["driverId"]
            input_file = os.path.join(input_directory, f'{driverId}.json')
            print("Current file:", input_file)
            output_file = os.path.join(output_directory, f'{driverId}.json')
            with open(input_file, 'r', encoding='utf-8') as file:
                data = json.load(file)
            data["lastUpdate"] = datetime.datetime.now().isoformat()
                # if(driverId=="max_verstappen"):
                #     print(data["seasonWins"])
            if not (result["status"] == "Finished" or '+' in result["status"]):
                data["DNFs"][season][raceName] = result["status"]
                data["seasonDNFs"][season] = len(data["DNFs"][season].keys())
                data["totalDNFs"] = sum(data["seasonDNFs"].values())
            if(result["position"]=="1" or result["position"]=="2" or result["position"]=="3"):
                # data["totalPodiums"] += 1
                # data["seasonPodiums"][season] += 1
                data["podiums"][season][raceName] = result["position"]
                data["seasonPodiums"][season] = len(data["podiums"][season].keys())
                data["totalPodiums"] = sum(data["seasonPodiums"].values())
            # if(result["grid"]=="1"):
            #     data["totalPoles"] += 1
            #     data["seasonPoles"][season] += 1
            if "FastestLap" in result:
                data["fastLaps"][season][raceName] = result["FastestLap"]["Time"]["time"]
            else:
                data["fastLaps"][season][raceName] = -1
            data["racePosition"][season]["positions"][raceName] = result["position"]
            if(result["position"]=="1"):
                data["seasonWins"][season] = list(data["racePosition"][season]["positions"].values()).count("1")
                data["totalWins"] = sum(data["seasonWins"].values())
                # data["totalWins"] += 1
                # data["seasonWins"][season] += 1
            data["qualiPosition"][season]["positions"][raceName] = result["grid"]
            url2 = f'https://ergast.com/api/f1/current/drivers/{driverId}/driverStandings.json'
            response2 = requests.get(url2)
            if response2.status_code == 200:
                responsedata2 = response2.json()
                driverStanding = responsedata2["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"][0]
                data["finalStandings"][season]["position"] = driverStanding["position"]
                data["finalStandings"][season]["points"] = driverStanding["points"]
                data["posAfterRace"][season]["pos"][raceName] = {}
                data["posAfterRace"][season]["pos"][raceName]["points"] = int(driverStanding["points"])
            else:
                print("url2", response2.status_code)
            
            url3 = f'https://ergast.com/api/f1/current/drivers/{driverId}/qualifying.json'
            response3 = requests.get(url3)
            if response3.status_code == 200:
                responsedata3 = response3.json()
                qualifyings = responsedata3["MRData"]["RaceTable"]["Races"]
                for qualifyingit in reversed(qualifyings):
                    if(qualifyingit["round"]==round):
                        qualifying = qualifyingit["QualifyingResults"][0]
                        if(qualifying["position"]=="1"):
                            data["poles"][season].append(raceName)
                            data["seasonPoles"][season] = len(data["poles"][season])
                            data["totalPoles"] = sum(data["seasonPoles"].values())
                        # if(driverId=="max_verstappen"):
                        if("Q3" in qualifying):
                            val3 = qualifying["Q3"]
                        else:
                            val3 = "N/A"
                        if("Q2" in qualifying):
                            val2 = qualifying["Q2"]
                        else:
                            val2 = "N/A"
                        if("Q1" in qualifying):
                            val1 = qualifying["Q1"]
                        else:
                            val1 = "N/A"
                        data["driverQualifyingTimes"][season]["QualiTimes"][raceName] = [val1, val2, val3]
                        
                        # print(data["driverQualifyingTimes"][season]["QualiTimes"][raceName])
                        break
                
            else:
                print("url3", response3.status_code)
            f = open(output_file, "w", encoding='utf-8')
            json.dump(data, f, indent=4, ensure_ascii=False, cls=NpEncoder)
            f.close()
            drivers_done += 1
            print(drivers_done, output_file)
            print("------------------------------")

    else:
        print(response1.status_code)

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

def update_races():
    season = 2024

    response = requests.get(f'https://api.openf1.org/v1/meetings?year={season}')
    if response.status_code==200:
        responsedata = response.json()
        with open('races/races.json', 'r', encoding='utf-8') as file:
            data = json.load(file)
        if(len(data[str(season)].values())!=len(responsedata)):
            currentRace = responsedata[-1]
            data[str(season)][currentRace["meeting_name"]] = {}
            data[str(season)][currentRace["meeting_name"]]["meeting_key"] = currentRace["meeting_key"]
            data[str(season)][currentRace["meeting_name"]]["location"] = currentRace["location"]
            with open('races/races.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False, cls=NpEncoder)
            with open('races/racesbyMK.json', 'r', encoding='utf-8') as g:
                result = json.load(g)
            result[currentRace["meeting_key"]] = {}
            result[currentRace["meeting_key"]]["raceName"] = currentRace["meeting_name"]
            result[currentRace["meeting_key"]]["location"] = currentRace["location"]
            result[currentRace["meeting_key"]]["year"] = str(season)
            with open('races/racesbyMK.json', 'w', encoding='utf-8') as g:
                json.dump(result, g, indent=4, ensure_ascii=False, cls=NpEncoder)
            
    print("Race Details updated successfully!")

def update_raceResults():
    for season in [2024]:
        with open(f'races/{season}/raceDetails.json','r',encoding='utf-8') as file:
                races = json.load(file)
        result = []
        for race in races:
            if dt.strptime(race['date'], '%Y-%m-%d') < dt.now():
                print(race['raceName'], season)
                url = f'https://ergast.com/api/f1/{season}/{race["round"]}/results.json'
                response = requests.get(url)
                # print(response)
                responsedata = response.json()
                result.append(responsedata['MRData']['RaceTable']['Races'][0])
                # result[race['round']] = responsedata['MRData']['RaceTable']['Races'][0]['Results']
            else:
                break
        with open(f'races/{season}/results.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=4, ensure_ascii=False, cls=NpEncoder)

    print("Race Results updated successfully!")

def update_qualifying():
    for season in [2024]:
        with open(f'races/{season}/raceDetails.json','r',encoding='utf-8') as file:
            races = json.load(file)
        result = []
        for race in races:
            if dt.strptime(race['date'], '%Y-%m-%d') < dt.now():
                print(race['raceName'], season)
                url = f'https://ergast.com/api/f1/{season}/{race["round"]}/qualifying.json'
                response = requests.get(url)
                # print(response)
                responsedata = response.json()
                result.append(responsedata['MRData']['RaceTable']['Races'][0])
                # result[race['round']] = responsedata['MRData']['RaceTable']['Races'][0]['Results']
            else:
                break
        with open(f'races/{season}/qualifying.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=4, ensure_ascii=False, cls=NpEncoder)

    print("Qualifying results updated successfully!")

def update_driverStandings():
    for season in [2024]:
        with open(f'races/{season}/raceDetails.json', 'r', encoding='utf-8') as file:
            races = json.load(file)
        result = {}
        for race in races:
            if dt.strptime(race['date'],'%Y-%m-%d') < dt.now():
                print(race['raceName'], season)
                url = f'https://ergast.com/api/f1/{season}/{race["round"]}/driverStandings.json'
                response = requests.get(url)
                if response.status_code==200:
                    responsedata = response.json()
                    result[race['round']] = responsedata['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']
                    prev = result[race['round']]
                else:
                    print(response.status_code)
            else:
                result['latest'] = prev
                break
        with open(f'races/{season}/driverStandings.json', 'w', encoding='utf-8') as file:
            json.dump(result, file, indent=4, ensure_ascii=False, cls=NpEncoder)

    print('Driver Standings updated successfully!')

def update_constructorStandings():
    for season in [2024]:
        with open(f'races/{season}/raceDetails.json', 'r', encoding='utf-8') as file:
            races = json.load(file)
        result = {}
        for race in races:
            if dt.strptime(race['date'],'%Y-%m-%d') < dt.now():
                print(race['raceName'], season)
                url = f'https://ergast.com/api/f1/{season}/{race["round"]}/constructorStandings.json'
                response = requests.get(url)
                if response.status_code==200:
                    responsedata = response.json()
                    result[race['round']] = responsedata['MRData']['StandingsTable']['StandingsLists'][0]['ConstructorStandings']
                    prev = result[race['round']]
                else:
                    print(response.status_code)
            else:
                result['latest'] = prev
                break
        with open(f'races/{season}/constructorStandings.json', 'w', encoding='utf-8') as file:
            json.dump(result, file, indent=4, ensure_ascii=False, cls=NpEncoder)

    print('Constructor Standings updated successfully')

def update():
    print("==========Updating constructors==========")
    update_constructors()
    print("==========Updating constructor drivers==========")
    update_constructor_drivers()
    print("==========Updating Driver Data==========")
    update_driverData()
    print("==========Analysing Driver Data==========")
    analyse_driverData()
    print("==========Replacing NaNs in Driver Data==========")
    replace_NaN()
    print("==========Updating Race Details==========")
    update_races()
    print("==========Updating Race Results==========")
    update_raceResults()
    print("==========Updating Qualifying Sessions==========")
    update_qualifying()
    print("==========Updating Driver Standings==========")
    update_driverStandings()
    print("==========Updating Constructor Standings==========")
    update_constructorStandings()

update()