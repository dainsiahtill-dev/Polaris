import shutil
from pathlib import Path

backend_root = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend')
config_dir = backend_root / 'polaris' / 'config'
config_dir.mkdir(parents=True, exist_ok=True)
(config_dir / '__init__.py').write_text('', encoding='utf-8')

# Move the configs
nats_src = backend_root / 'polaris' / 'infrastructure' / 'messaging' / 'internal' / 'nats_config.py'
llm_src = backend_root / 'polaris' / 'kernelone' / 'llm' / 'internal' / 'llm_config.py'
pm_src = backend_root / 'polaris' / 'cells' / 'orchestration' / 'pm_planning' / 'internal' / 'pm_config.py'
director_src = backend_root / 'polaris' / 'cells' / 'director' / 'internal' / 'director_config.py'

if nats_src.exists(): shutil.move(str(nats_src), str(config_dir / 'nats_config.py'))
if llm_src.exists(): shutil.move(str(llm_src), str(config_dir / 'llm_config.py'))
if pm_src.exists(): shutil.move(str(pm_src), str(config_dir / 'pm_config.py'))
if director_src.exists(): shutil.move(str(director_src), str(config_dir / 'director_config.py'))

# Update polaris/bootstrap/config.py imports
boot_config = backend_root / 'polaris' / 'bootstrap' / 'config.py'
content = boot_config.read_text(encoding='utf-8')

content = content.replace('from polaris.cells.director.internal.director_config import DirectorConfig', 'from polaris.config.director_config import DirectorConfig')
content = content.replace('from polaris.cells.orchestration.pm_planning.internal.pm_config import PMConfig', 'from polaris.config.pm_config import PMConfig')
content = content.replace('from polaris.kernelone.llm.internal.llm_config import LLMConfig', 'from polaris.config.llm_config import LLMConfig')
content = content.replace('from polaris.infrastructure.messaging.internal.nats_config import NATSConfig', 'from polaris.config.nats_config import NATSConfig')

boot_config.write_text(content, encoding='utf-8')
print('Configs moved to polaris/config/')
