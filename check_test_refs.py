import os
import subprocess

not_imported = [
    'polaris.cells.code_intelligence.engine.internal',
    'polaris.cells.code_intelligence.engine.internal.adapters',
    'polaris.cells.code_intelligence.engine.public',
    'polaris.cells.cognitive.knowledge_distiller.internal.tests',
    'polaris.cells.cognitive.knowledge_distiller.public.tests',
    'polaris.cells.director.delivery.internal',
    'polaris.cells.director.delivery.public',
    'polaris.cells.director.runtime.internal',
    'polaris.cells.director.runtime.public',
    'polaris.cells.director.taskling.tests',
    'polaris.cells.orchestration.workflow_orchestration.internal',
    'polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.workflow',
    'polaris.kernelone.benchmark._archived',
    'polaris.kernelone.policy',
    'polaris.kernelone.task_graph',
    'polaris.tests.delivery.cli',
    'polaris.tests.unit.cells.cognitive',
    'polaris.tests.unit.cells.cognitive.knowledge_distiller',
    'polaris.tests.unit.cells.docs',
    'polaris.tests.unit.cells.docs.court_workflow',
]

for mp in not_imported:
    pattern = mp.replace('.', r'\.')
    result = subprocess.run(
        ['rg', '-n', '--type', 'py', pattern, 'src/backend/polaris/tests/'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    refs = []
    if result.returncode == 0:
        for line in result.stdout.strip().split('\n'):
            if line.strip() and '__pycache__' not in line:
                refs.append(line)
    
    if refs:
        print(f'REF_IN_TESTS: {mp}')
        for ref in refs[:3]:
            print(f'  {ref}')
        if len(refs) > 3:
            print(f'  ... and {len(refs)-3} more')
        print()
    else:
        print(f'NO_TEST_REFS: {mp}')
