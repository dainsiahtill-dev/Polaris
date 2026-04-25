import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
try:
    with open(path, encoding='utf-8') as f:
        print(f"--- Parsing {path} ---")
        for i, line in enumerate(f):
            data = json.loads(line)
            event = data.get('event')
            role = data.get('role')
            print(f"[{i}] Event: {event} | Role: {role}")
            if event == 'llm_call_start':
                # look at messages or prompt tokens
                msgs = data.get('data', {}).get('metadata', {}).get('messages', [])
                if msgs:
                    print(f"   Last User Msg: {msgs[-1].get('content', '')[:100]}...")
            elif event == 'llm_call_end':
                content = data.get('data', {}).get('response', {}).get('content', '')
                print(f"   Content Length: {len(content)}")
                if 'single_batch_contract_violation' in content or 'error' in content.lower():
                    print(f"   Content Snippet: {content[:100]}...")
            elif event == 'tool_call':
                tool = data.get('data', {}).get('tool')
                args = data.get('data', {}).get('args')
                print(f"   Tool: {tool} | Args: {args}")
            elif event == 'tool_result':
                res = data.get('data', {}).get('result', {})
                print(f"   Result Success: {res.get('success')} | Data len: {len(str(res))}")
            elif event in ['llm_error', 'tool_error', 'error']:
                print(f"   ERROR: {data.get('data')}")
except Exception as e:
    print(f"Error reading file: {e}")
