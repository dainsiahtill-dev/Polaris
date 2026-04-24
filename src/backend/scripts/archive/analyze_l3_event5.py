import json
path = r'X:\.polaris\projects\l3-file-edit-sequence-14fa457f9d07-9251434a1e84\runtime\events\director.llm.events.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if i == 5:
            d = data.get('data', {})
            meta = d.get('metadata', {})
            print(f'=== Event [5] raw stream_raw_tool_call_count={meta.get("stream_raw_tool_call_count")} ===')
            print(f'   stream_deduped_tool_call_count={meta.get("stream_deduped_tool_call_count")}')
            print(f'   response_content: {repr(meta.get("response_content", "")[:500])}')
        if i == 6:
            d = data.get('data', {})
            print(f'=== Event [6] tool_call ===')
            print(f'   tool: {d.get("tool")}')
            print(f'   args: {d.get("args")}')
            print(f'   run_id: {data.get("run_id")}')
            print(f'   data keys: {list(d.keys())}')
