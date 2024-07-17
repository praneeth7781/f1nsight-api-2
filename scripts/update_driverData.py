import requests
import json
import os
import datetime

url1 = 'https://ergast.com/api/f1/current/last/results.json'

response1=requests.get(url1)

input_directory = '../drivers/'
output_directory = '../drivers2/'
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
            data["DNFs"][season] = result["status"]
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
                        data["poles"][season][raceName] = qualifying["Q3"]
                        data["seasonPoles"][season] = len(data["poles"][season].keys())
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
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.close()
        drivers_done += 1
        print(drivers_done, output_file)
        print("------------------------------")

else:
    print(response1.status_code)

print("update_driverData.py run successfully!")