import requests
import json
from datetime import datetime

for season in [2024]:
    with open(f'../races/{season}/raceDetails.json', 'r', encoding='utf-8') as file:
        races = json.load(file)
    result = {}
    for race in races:
        if datetime.strptime(race['date'],'%Y-%m-%d') < datetime.now():
            print(race['raceName'], season)
            url = f'https://ergast.com/api/f1/{season}/{race['round']}/constructorStandings.json'
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
    with open(f'../races/{season}/constructorStandings.json', 'w', encoding='utf-8') as file:
        json.dump(result, file, indent=4, ensure_ascii=False)

print('update_constructorStandings.py run successfully')