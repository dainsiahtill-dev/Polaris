import os
import subprocess

candidates = [
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
]

for mp in candidates:
    # Search in yaml, json, md, txt files
    for ext in ['yaml', 'yml', 'json', 'md', 'txt']:
        result = subprocess.run(
            ['rg', '-n', '--type', ext, mp.replace('.', r'\.'), '.'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [l for l in result.stdout.strip().split('\n') if '__pycache__' not in l]
            if lines:
                print(f'REF_IN_{ext.upper()}: {mp}')
                for line in lines[:3]:
                    print(f'  {line}')
                print()
                break
    else:
        print(f'NO_CONFIG_REFS: {mp}')
