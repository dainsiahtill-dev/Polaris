import json, os
p=os.environ['CONTRACT']
with open(p, encoding='utf-8') as f: data=json.load(f)
print('score', data.get('quality_gate',{}).get('score'), 'critical', data.get('quality_gate',{}).get('critical_issue_count'), 'tasks', len(data.get('tasks') or []))
for t in (data.get('tasks') or [])[:16]:
    print(t.get('id') or t.get('task_id'), '|', t.get('title') or t.get('subject'), '|', t.get('metadata',{}).get('autofix_reason'), '|', t.get('scope_paths') or t.get('target_files'))
