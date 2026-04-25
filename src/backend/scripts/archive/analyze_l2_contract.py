import json

path = r'X:\.polaris\projects\l2-path-inference-bacd65b0c497-eee7f83fd6bc\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i == 5:
            d = data.get('data', {})
            meta = d.get('metadata', {})
            print('=== Event [5] msg[5] TASK CONTRACT full content ===')
            msgs = meta.get('messages', [])
            if msgs:
                print(msgs[-1].get('content', ''))
        if i == 0:
            d = data.get('data', {})
            meta = d.get('metadata', {})
            msgs = meta.get('messages', [])
            for j, m in enumerate(msgs):
                if j == 5:
                    print('=== msg[5] TASK CONTRACT full ===')
                    print(m.get('content', ''))
