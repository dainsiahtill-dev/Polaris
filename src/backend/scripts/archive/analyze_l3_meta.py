import json
path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i in [1, 3, 5]:
            d = data.get('data', {})
            print(f'=== Event [{i}] ===')
            print(f'  tool_calls_count: {d.get("tool_calls_count")}')
            print(f'  completion_tokens: {d.get("completion_tokens")}')
            # dig into metadata more carefully
            meta = d.get('metadata', {})
            print(f'  All metadata keys: {list(meta.keys())}')
            for k, v in meta.items():
                if k != 'messages':
                    print(f'  meta.{k}: {v}')
