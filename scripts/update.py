import subprocess

files_to_run = [
    'update_constructors.py',
    'update_constructor_drivers.py',
    'update_driverData.py',
    'update_driverDataAnalysis.py',
    'update_replaceNan.py',
    'update_races.py',
    'update_raceResults.py',
    'update_qualifying.py',
    'update_driverStandings.py',
    'update_constructorStandings.py'
]

# Loop through the files and run them one by one
for file in files_to_run:
    print("Running:", file)
    result = subprocess.run(['python', file])
    if result.returncode != 0:
        print(f"Error running {file}")
        break