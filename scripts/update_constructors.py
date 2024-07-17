import requests, json

season = 2024

response = requests.get(f'https://ergast.com/api/f1/{season}/constructors.json')
if response.status_code==200:
    responsedata = response.json()
    constructors = responsedata["MRData"]["ConstructorTable"]["Constructors"]
    # print(constructors)
    apx = {
        "constructorId": "apx",
        "url": "https://en.wikipedia.org/wiki/Untitled_Joseph_Kosinski_film",
        "name": "APXGP",
        "nationality": "American"
    }
    constructors.append(apx)
    with open(f'../constructors/{season}.json', 'w', encoding='utf-8') as file:
        json.dump(constructors, file, ensure_ascii=False, indent=4)
    print("update_constructors.py run successfully!")
else:
    print(response.status_code)