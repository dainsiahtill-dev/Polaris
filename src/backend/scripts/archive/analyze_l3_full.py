import json

path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i in [0, 1, 3, 5]:
            print(f'=== Event [{i}] ===')
            print(f'  event: {data.get("event")}')
            d = data.get('data', {})
            print(f'  prompt_tokens: {d.get("prompt_tokens")}')
            print(f'  completion_tokens: {d.get("completion_tokens")}')
            print(f'  tool_calls_count: {d.get("tool_calls_count")}')
            print(f'  error_message: {d.get("error_message")}')
            print(f'  error_category: {d.get("error_category")}')
            meta = d.get('metadata', {})
            print(f'  finish_reason: {meta.get("finish_reason")}')
            print(f'  stop_reason: {meta.get("stop_reason")}')
            print(f'  tool_choice: {meta.get("tool_choice")}')
            print(f'  stream: {meta.get("stream")}')
