import re
from pathlib import Path

config_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/bootstrap/config.py')
content = config_path.read_text(encoding='utf-8')

# Remove the global import
content = re.sub(
    r'from polaris\.cells\.policy\.workspace_guard\.public\.service import \(\n\s+SELF_UPGRADE_MODE_ENV,\n\s+ensure_workspace_target_allowed,\n\)',
    '',
    content
)
content = re.sub(
    r'from polaris\.cells\.policy\.workspace_guard\.public\.service import .*',
    '',
    content
)

# Insert local import in apply_update
apply_update_pattern = r'(def apply_update\(self, update: SettingsUpdate\) -> None:\n\s+"""Apply partial update payload\."""\n)'
content = re.sub(apply_update_pattern, r'\1        from polaris.cells.policy.workspace_guard.public.service import ensure_workspace_target_allowed\n', content)

# Insert local import in from_env
from_env_pattern = r'(def from_env\(cls\) -> Settings:\n\s+"""Create settings from environment variables\."""\n)'
content = re.sub(from_env_pattern, r'\1        from polaris.cells.policy.workspace_guard.public.service import ensure_workspace_target_allowed, SELF_UPGRADE_MODE_ENV\n', content)

config_path.write_text(content, encoding='utf-8')
print("Fixed circular import")
