import os
import json
import numpy as np
import math
import datetime

input_directory = '../drivers2/'
output_directory = '../drivers2/'

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
            pole_races = data["poles"][season].keys()
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
    json.dump(processed_data, f, indent=4, ensure_ascii=False)
    f.close()
    print(files_done, input_file, output_file)
    print("---------------------------------------------")

print("update_driverDataAnalysis.py run successfully!")