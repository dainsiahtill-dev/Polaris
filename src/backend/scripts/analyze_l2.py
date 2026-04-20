import json
path = r'X:\.polaris\projects\l2-path-inference-bacd65b0c497-eee7f83fd6bc\runtime\events\director.llm.events.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        event = data.get('event')
        d = data.get('data', {})
        print(f'[{i}] Event: {event}')
        if event == 'llm_call_start':
            meta = d.get('metadata', {})
            msgs = meta.get('messages', [])
            print(f'   prompt_tokens={d.get("prompt_tokens")} msgs={len(msgs)} stream={meta.get("stream")}')
        elif event == 'llm_call_end':
            meta = d.get('metadata', {})
            print(f'   completion_tokens={d.get("completion_tokens")} tool_calls_count={d.get("tool_calls_count")} error={d.get("error_message")} finish={meta.get("finish_reason")} raw_tc={meta.get("stream_raw_tool_call_count")}')
        elif event == 'tool_call':
            print(f'   Tool: {d.get("tool")} | Args: {d.get("args")}')
        elif event == 'tool_result':
            res = d.get('result', {})
            print(f'   success={res.get("success")} error={res.get("error")} loop_break={res.get("loop_break")}')
        elif event in ('llm_error', 'error', 'tool_error'):
            print(f'   *** ERROR: {d}')
