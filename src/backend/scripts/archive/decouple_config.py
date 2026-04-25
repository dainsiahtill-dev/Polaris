import re
from pathlib import Path

backend_root = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend')
config_path = backend_root / 'polaris' / 'bootstrap' / 'config.py'

content = config_path.read_text(encoding='utf-8')

# We'll use regex to extract the class definitions.

def extract_class(class_name, content):
    pattern = re.compile(rf'(class {class_name}\(BaseModel\):.*?(?=\nclass |\n# ═|\Z))', re.DOTALL)
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return None

nats_config = extract_class('NATSConfig', content)
llm_config = extract_class('LLMConfig', content)
pm_config = extract_class('PMConfig', content)
director_config = extract_class('DirectorConfig', content)

# Write NATSConfig
if nats_config:
    nats_dir = backend_root / 'polaris' / 'infrastructure' / 'messaging' / 'internal'
    nats_dir.mkdir(parents=True, exist_ok=True)
    nats_path = nats_dir / 'nats_config.py'
    nats_path.write_text("from pydantic import BaseModel, Field, field_validator\nfrom typing import Any\n\n" + nats_config + "\n", encoding='utf-8')
    content = content.replace(nats_config, "")
    content = "from polaris.infrastructure.messaging.internal.nats_config import NATSConfig\n" + content

# Write LLMConfig
if llm_config:
    llm_dir = backend_root / 'polaris' / 'kernelone' / 'llm' / 'internal'
    llm_dir.mkdir(parents=True, exist_ok=True)
    llm_path = llm_dir / 'llm_config.py'
    llm_path.write_text("from pydantic import BaseModel, Field\n\n" + llm_config + "\n", encoding='utf-8')
    content = content.replace(llm_config, "")
    content = "from polaris.kernelone.llm.internal.llm_config import LLMConfig\n" + content

# Write PMConfig
if pm_config:
    pm_dir = backend_root / 'polaris' / 'cells' / 'orchestration' / 'pm_planning' / 'internal'
    pm_dir.mkdir(parents=True, exist_ok=True)
    pm_path = pm_dir / 'pm_config.py'
    pm_path.write_text("from pydantic import BaseModel, Field\n\n" + pm_config + "\n", encoding='utf-8')
    content = content.replace(pm_config, "")
    content = "from polaris.cells.orchestration.pm_planning.internal.pm_config import PMConfig\n" + content

# Write DirectorConfig
if director_config:
    director_dir = backend_root / 'polaris' / 'cells' / 'director' / 'internal'
    director_dir.mkdir(parents=True, exist_ok=True)
    director_path = director_dir / 'director_config.py'
    director_path.write_text("from pydantic import BaseModel, Field, field_validator\nfrom typing import Any\n\n" + director_config + "\n", encoding='utf-8')
    content = content.replace(director_config, "")
    content = "from polaris.cells.director.internal.director_config import DirectorConfig\n" + content

# Write the updated config.py back
# Clean up multiple empty lines
content = re.sub(r'\n{3,}', '\n\n', content)
config_path.write_text(content, encoding='utf-8')
print("Successfully decoupled configs.")
