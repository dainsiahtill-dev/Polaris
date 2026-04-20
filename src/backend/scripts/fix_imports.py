import re
from pathlib import Path

config_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/bootstrap/config.py')
content = config_path.read_text(encoding='utf-8')

# Find all 'from polaris.*' lines that we just prepended
lines = content.split('\n')
imports = []
rest = []
for line in lines:
    if line.startswith('from polaris.') and 'Config' in line:
        imports.append(line)
    else:
        rest.append(line)

content = '\n'.join(rest)
# Now insert the imports after 'from __future__ import annotations'
future_import = 'from __future__ import annotations'
if future_import in content:
    content = content.replace(future_import, future_import + '\n\n' + '\n'.join(imports))
else:
    content = '\n'.join(imports) + '\n\n' + content

config_path.write_text(content, encoding='utf-8')
print("Fixed future imports")
