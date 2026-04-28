import os
import subprocess

dirs = [
    'src/backend/polaris/cells/adapters',
    'src/backend/polaris/cells/architect',
    'src/backend/polaris/cells/archive',
    'src/backend/polaris/cells/audit',
    'src/backend/polaris/cells/chief_engineer',
    'src/backend/polaris/cells/code_intelligence/engine/internal',
    'src/backend/polaris/cells/code_intelligence/engine/internal/adapters',
    'src/backend/polaris/cells/code_intelligence/engine/public',
    'src/backend/polaris/cells/cognitive/knowledge_distiller/internal/tests',
    'src/backend/polaris/cells/cognitive/knowledge_distiller/public/tests',
    'src/backend/polaris/cells/context',
    'src/backend/polaris/cells/delivery',
    'src/backend/polaris/cells/director',
    'src/backend/polaris/cells/director/delivery/internal',
    'src/backend/polaris/cells/director/delivery/public',
    'src/backend/polaris/cells/director/runtime/internal',
    'src/backend/polaris/cells/director/runtime/public',
    'src/backend/polaris/cells/director/taskling/tests',
    'src/backend/polaris/cells/docs',
    'src/backend/polaris/cells/events',
    'src/backend/polaris/cells/factory',
    'src/backend/polaris/cells/finops',
    'src/backend/polaris/cells/llm',
    'src/backend/polaris/cells/orchestration/workflow_engine/internal',
    'src/backend/polaris/cells/orchestration/workflow_orchestration/internal',
    'src/backend/polaris/cells/orchestration/workflow_runtime/internal/runtime_engine',
    'src/backend/polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/workflow',
    'src/backend/polaris/cells/policy',
    'src/backend/polaris/cells/policy/protocol',
    'src/backend/polaris/cells/qa',
    'src/backend/polaris/cells/resident',
    'src/backend/polaris/cells/runtime',
    'src/backend/polaris/cells/storage',
    'src/backend/polaris/cells/workspace',
    'src/backend/polaris/infrastructure/audit',
    'src/backend/polaris/infrastructure/db',
    'src/backend/polaris/infrastructure/messaging',
    'src/backend/polaris/infrastructure/realtime',
    'src/backend/polaris/kernelone/auth_context',
    'src/backend/polaris/kernelone/benchmark/_archived',
    'src/backend/polaris/kernelone/context/auth_context',
    'src/backend/polaris/kernelone/contracts',
    'src/backend/polaris/kernelone/policy',
    'src/backend/polaris/kernelone/task_graph',
    'src/backend/polaris/kernelone/tools',
    'src/backend/polaris/tests/delivery/cli',
    'src/backend/polaris/tests/unit/cells/cognitive',
    'src/backend/polaris/tests/unit/cells/cognitive/knowledge_distiller',
    'src/backend/polaris/tests/unit/cells/docs',
    'src/backend/polaris/tests/unit/cells/docs/court_workflow',
]

for d in dirs:
    parts = d.replace('\\', '/').split('/')
    module_path = '.'.join(parts[2:])
    
    # Check if module or any submodule is imported
    result = subprocess.run(
        ['rg', '-n', '--type', 'py', f'(?:from|import)\\s+{module_path.replace(".", "\\.")}(?:\\s|\\.|$)', '.'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    refs = []
    if result.returncode == 0:
        for line in result.stdout.strip().split('\n'):
            if not line.strip() or '__pycache__' in line:
                continue
            # Skip self-reference in its own __init__.py
            if d.replace('/', '\\') in line and '__init__.py' in line:
                continue
            refs.append(line)
    
    if refs:
        print(f'IMPORTED: {module_path} ({len(refs)} refs)')
    else:
        print(f'NOT_IMPORTED: {module_path}')
