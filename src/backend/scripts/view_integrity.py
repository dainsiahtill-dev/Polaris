import json
path = 'X:/.polaris/projects/l5-context-integrity-e7cab6315853-855668b9e0bf/runtime/events/director.llm.events.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        event = data.get('event')
        if event == 'tool_call':
            print(f'[{i}] Tool: {data["data"]["tool"]} Args: {data["data"].get("args")}')
        elif event == 'tool_error':
            print(f'[{i}] Tool Error: {data["data"]}')
        elif event == 'tool_result':
            print(f'[{i}] Tool Result success={data["data"].get("result", {}).get("success")}')
