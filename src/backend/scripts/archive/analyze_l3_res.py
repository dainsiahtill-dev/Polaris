import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        if i in [7, 11]:
            data = json.loads(line)
            print(f"[{i}] Result: {data.get('data', {}).get('result')}")
