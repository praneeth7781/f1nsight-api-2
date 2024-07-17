import math
import os
import json
import numpy as np
import shutil

input_directory = '../drivers2/'
output_directory = '../drivers/'

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
    json.dump(updated_data, f, indent=4, ensure_ascii=False)
    f.close()
    print(files_done, input_file, output_file)
    print("======================================")
    
shutil.rmtree('../drivers2')
print("update_replaceNan.py run successfully!")