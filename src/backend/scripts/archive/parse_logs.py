import json

paths = [
    'X:/.polaris/projects/l5-context-integrity-e7cab6315853-855668b9e0bf/runtime/events/director.llm.events.jsonl',
    'X:/.polaris/projects/l5-sequential-dag-38d62445a0e5-25f5c2e421c9/runtime/events/director.llm.events.jsonl'
]

for path in paths:
    print(f'\n=== File: {path} ===')
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                data = json.loads(line)
                event = data.get('event')
                d = data.get('data', {})
                if event == 'llm_call_end':
                    metadata = d.get('metadata', {})
                    tools = d.get('tool_calls_count', 0)
                    response = metadata.get('response_content', '')
                    print(f'[llm_call_end] Tools: {tools} | Text: {response[:200]!r}...')
                elif event == 'llm_call_start':
                    messages = d.get('metadata', {}).get('messages', [])
                    print(f'[llm_call_start] Turn round: {d.get("metadata", {}).get("turn_round", 0)} | Messages: {len(messages)}')
                    if messages:
                        print(f'   -> Last user prompt: {messages[-1].get("content", "")[:200]!r}...')
                elif event == 'llm_error':
                    print(f'[llm_error] Error: {d}')
                else:
                    print(f'[{event}]')
    except Exception as e:
        print(f"Error reading file: {e}")
