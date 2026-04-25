
# Look for the case definition
import os

fixture_path = r'c:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\cells\llm\evaluation\fixtures\tool_calling_matrix\cases\l3_file_edit_sequence.json'
if os.path.exists(fixture_path):
    print(open(fixture_path, encoding='utf-8').read())
else:
    # Search for it
    for root, dirs, files in os.walk(r'c:\Users\dains\Documents\GitLab\polaris\src\backend'):
        for file in files:
            if 'l3_file_edit' in file.lower() or 'l3-file-edit' in file.lower():
                print(os.path.join(root, file))
