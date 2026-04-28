import os

safe = [
    'src/backend/polaris/cells/code_intelligence/engine/internal/adapters',
    'src/backend/polaris/cells/code_intelligence/engine/public',
    'src/backend/polaris/cells/cognitive/knowledge_distiller/internal/tests',
    'src/backend/polaris/cells/cognitive/knowledge_distiller/public/tests',
    'src/backend/polaris/cells/director/delivery/internal',
    'src/backend/polaris/cells/director/delivery/public',
    'src/backend/polaris/cells/director/runtime/internal',
    'src/backend/polaris/cells/director/runtime/public',
    'src/backend/polaris/cells/director/taskling/tests',
    'src/backend/polaris/cells/orchestration/workflow_orchestration/internal',
    'src/backend/polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/workflow',
    'src/backend/polaris/kernelone/benchmark/_archived',
]

safe_norm = [s.replace('\\', '/') for s in safe]

parents = set()
for d in safe:
    parents.add(os.path.dirname(d))

for parent in sorted(parents):
    items = os.listdir(parent)
    files = [f for f in items if os.path.isfile(os.path.join(parent, f)) and not f.endswith('.pyc')]
    dirs = [d for d in items if os.path.isdir(os.path.join(parent, d)) and d != '__pycache__']
    remaining_dirs = [d for d in dirs if os.path.join(parent, d).replace('\\', '/') not in safe_norm]
    would_be_empty = (files == ['__init__.py'] and not remaining_dirs)
    print(f'{parent}: files={files}, dirs={remaining_dirs}, would_be_empty={would_be_empty}')
