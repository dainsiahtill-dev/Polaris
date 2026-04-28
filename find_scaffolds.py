import os

def is_empty_dir(path):
    items = os.listdir(path)
    files = [f for f in items if os.path.isfile(os.path.join(path, f)) and not f.endswith('.pyc')]
    dirs = [d for d in items if os.path.isdir(os.path.join(path, d)) and d != '__pycache__']
    return not files and not dirs

def find_scaffolds(root):
    scaffolds = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        dirnames[:] = [d for d in dirnames if d not in ('node_modules', '.git', '__pycache__', '.pytest_cache', '.venv', 'venv')]
        if '__init__.py' not in filenames:
            continue
        files = [f for f in filenames if not f.endswith('.pyc')]
        if files != ['__init__.py']:
            continue
        dirs = [d for d in dirnames if d != '__pycache__']
        all_empty = True
        for d in dirs:
            subpath = os.path.join(dirpath, d)
            if not is_empty_dir(subpath):
                all_empty = False
                break
        if all_empty:
            scaffolds.append(dirpath)
    return scaffolds

root = 'src/backend/polaris'
scaffolds = find_scaffolds(root)
print(f'Total empty scaffold dirs: {len(scaffolds)}')
for s in sorted(scaffolds):
    print(s)
