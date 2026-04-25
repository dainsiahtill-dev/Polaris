import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        if i in [1, 3, 5]:
            data = json.loads(line)
            res = data.get('data', {}).get('response', {})
            print(f"[{i}] Content Length: {len(res.get('content', ''))}")
            print(f"[{i}] Tool Calls Count: {len(res.get('tool_calls', []))}")
            if res.get('tool_calls'):
                 for t in res['tool_calls']:
                     print(f"   Tool: {t.get('function', {}).get('name')} | Args: {t.get('function', {}).get('arguments')}")
