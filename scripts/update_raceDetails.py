import json
import requests

for season in [2024]:
    url = f'https://ergast.com/api/f1/{season}.json'
    response = requests.get(url)
    if response.status_code == 200:
        responsedata = response.json()
        with open(f'../races/{season}/raceDetails.json', 'w', encoding='utf-8') as file:
            json.dump(responsedata['MRData']['RaceTable']['Races'], file, indent=4, ensure_ascii=False)