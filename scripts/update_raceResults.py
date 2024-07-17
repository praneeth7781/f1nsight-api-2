import requests
import json
from datetime import datetime

for season in [2024]:
    with open(f'../races/{season}/raceDetails.json','r',encoding='utf-8') as file:
            races = json.load(file)
    result = []
    for race in races:
        if datetime.strptime(race['date'], '%Y-%m-%d') < datetime.now():
            print(race['raceName'], season)
            url = f'https://ergast.com/api/f1/{season}/{race['round']}/results.json'
            response = requests.get(url)
            # print(response)
            responsedata = response.json()
            result.append(responsedata['MRData']['RaceTable']['Races'][0])
            # result[race['round']] = responsedata['MRData']['RaceTable']['Races'][0]['Results']
        else:
            break
    with open(f'../races/{season}/results.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=4, ensure_ascii=False)

print("update_raceResults.py run successfully!")