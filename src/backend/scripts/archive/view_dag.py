import json

path = 'X:/.polaris/projects/l5-sequential-dag-38d62445a0e5-25f5c2e421c9/runtime/events/director.llm.events.jsonl'
with open(path, encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        event = data.get('event')
        print(f'[{i}] Event: {event}')
        if event == 'tool_call':
            print(f'   Tool: {data["data"]["tool"]} Args: {data["data"].get("args")}')
        elif event == 'tool_result':
            res = data["data"].get("result", {})
            print(f'   Result: success={res.get("success")} loop_break={res.get("loop_break")}')
