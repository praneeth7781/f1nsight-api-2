import requests, os, json

season = 2024

with open(f'../constructors/{2024}.json', 'r', encoding='utf-8') as file:
    data = json.load(file)

def fetchDrivers(team):
    print(team)
    response = requests.get(f'https://ergast.com/api/f1/{season}/constructors/{team}/drivers.json')
    if response.status_code==200:
        responsedata = response.json()
        drivers = responsedata["MRData"]["DriverTable"]["Drivers"]
        with open(f'../constructors/{season}/{team}.json', 'w', encoding='utf-8') as file:
            json.dump(drivers, file, ensure_ascii=False, indent=4)
    else:
        print(response.status_code)

for team in data:
    if team["constructorId"]!="apx":
        fetchDrivers(team["constructorId"])

print("update_constructor_drivers.py run successfully!")