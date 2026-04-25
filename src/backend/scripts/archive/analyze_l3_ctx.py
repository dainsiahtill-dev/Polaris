import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i in [2, 4]:  # llm_call_start events before the empty responses
            print(f'=== Event [{i}] - llm_call_start ===')
            d = data.get('data', {})
            meta = d.get('metadata', {})
            msgs = meta.get('messages', [])
            print(f'  message count: {len(msgs)}')
            for j, m in enumerate(msgs):
                role = m.get('role')
                content = m.get('content', '')
                tc = m.get('tool_calls', [])
                tr = m.get('tool_call_id', '')
                if isinstance(content, str):
                    c_preview = content[:200]
                else:
                    c_preview = str(content)[:200]
                print(f'  msg[{j}] role={role} len={len(str(content))} tc={len(tc)} tool_call_id={tr}')
                print(f'    preview: {c_preview}')
