import requests, json

season = 2024

response = requests.get(f'https://api.openf1.org/v1/meetings?year={season}')
if response.status_code==200:
    responsedata = response.json()
    with open('../races/races.json', 'r', encoding='utf-8') as file:
        data = json.load(file)
    if(len(data[str(season)].values())!=len(responsedata)):
        currentRace = responsedata[-1]
        data[str(season)][currentRace["meeting_name"]] = {}
        data[str(season)][currentRace["meeting_name"]]["meeting_key"] = currentRace["meeting_key"]
        data[str(season)][currentRace["meeting_name"]]["location"] = currentRace["location"]
        with open('../races/races.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        with open('../races/racesbyMK.json', 'r', encoding='utf-8') as g:
            result = json.load(g)
        result[currentRace["meeting_key"]] = {}
        result[currentRace["meeting_key"]]["raceName"] = currentRace["meeting_name"]
        result[currentRace["meeting_key"]]["location"] = currentRace["location"]
        result[currentRace["meeting_key"]]["year"] = str(season)
        with open('../races/racesbyMK.json', 'w', encoding='utf-8') as g:
            json.dump(result, g, indent=4, ensure_ascii=False)
        
print("update_races.py run successfully!")