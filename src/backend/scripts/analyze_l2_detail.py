import json
path = r'X:\.polaris\projects\l2-path-inference-bacd65b0c497-eee7f83fd6bc\runtime\events\director.llm.events.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i == 0:
            d = data.get('data', {})
            meta = d.get('metadata', {})
            msgs = meta.get('messages', [])
            print(f'=== Event [0] llm_call_start - Messages ===')
            for j, m in enumerate(msgs):
                role = m.get('role')
                content = str(m.get('content', ''))
                print(f'  msg[{j}] role={role}:')
                print(f'    {content[:400]}')
                print()
        if i == 3:
            d = data.get('data', {})
            res = d.get('result', {})
            print(f'=== Event [3] tool_result - Full result ===')
            print(json.dumps(res, indent=2, ensure_ascii=False)[:800])
