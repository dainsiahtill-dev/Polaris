import re
from pathlib import Path

backend_root = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend')
config_path = backend_root / 'config.py'

if not config_path.exists():
    print('config.py not found!')
    exit(1)

content = config_path.read_text(encoding='utf-8')

# We'll move the monolithic config.py to polaris/config_shim.py first
shim_dir = backend_root / 'polaris' / 'bootstrap'
shim_dir.mkdir(parents=True, exist_ok=True)
shim_path = shim_dir / 'config.py'

# Write the shim
shim_path.write_text(content, encoding='utf-8')

# Now rewrite imports in all python files
import glob

py_files = glob.glob(str(backend_root / '**' / '*.py'), recursive=True)

patterns = [
    (re.compile(r'from src\.backend\.config import'), r'from polaris.bootstrap.config import'),
    (re.compile(r'from backend\.config import'), r'from polaris.bootstrap.config import'),
    (re.compile(r'import src\.backend\.config'), r'import polaris.bootstrap.config'),
    (re.compile(r'import backend\.config'), r'import polaris.bootstrap.config'),
    # Also relative imports if they exist at root
    (re.compile(r'from polaris.bootstrap.config import'), r'from polaris.bootstrap.config import'),
]

modified_count = 0
for file_path in py_files:
    if str(file_path).endswith('src\\\\backend\\\\config.py'):
        continue
    if str(file_path).endswith('src/backend/config.py'):
        continue

    try:
        file_content = Path(file_path).read_text(encoding='utf-8')
        new_content = file_content
        for pattern, replacement in patterns:
            new_content = pattern.sub(replacement, new_content)

        if new_content != file_content:
            Path(file_path).write_text(new_content, encoding='utf-8')
            modified_count += 1
            print(f'Updated imports in {file_path}')
    except Exception as e:
        print(f'Error reading {file_path}: {e}')

# Remove original config.py
config_path.unlink()
print(f'Successfully moved config.py to polaris/bootstrap/config.py and updated {modified_count} files.')
