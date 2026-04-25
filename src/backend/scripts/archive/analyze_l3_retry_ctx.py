import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i in [2, 4]:
            print(f'=== Event [{i}] ===')
            d = data.get('data', {})
            meta = d.get('metadata', {})
            msgs = meta.get('messages', [])
            print(f'  message count: {len(msgs)}')
            for j, m in enumerate(msgs):
                role = m.get('role')
                content = m.get('content', '')
                print(f'  --- msg[{j}] role={role} ---')
                print(f'  {str(content)[:400]}')
