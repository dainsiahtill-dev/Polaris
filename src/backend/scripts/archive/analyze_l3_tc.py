import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i in [1, 3, 5]:
            print(f'=== Event [{i}] - llm_call_end tool_calls ===')
            d = data.get('data', {})
            # Find tool calls in metadata
            meta = d.get('metadata', {})
            tool_calls = meta.get('tool_calls', [])
            print(f'  metadata.tool_calls: {tool_calls}')
            # Try response
            resp = d.get('response', {})
            print(f'  response.tool_calls: {resp.get("tool_calls", [])}')
            print(f'  response.content: {resp.get("content", "")[:200]!r}')
            print(f'  Full data keys: {list(d.keys())}')
