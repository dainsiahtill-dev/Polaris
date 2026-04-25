import sys

sys.path.insert(0, r'c:\Users\dains\Documents\GitLab\polaris\src\backend')
from polaris.cells.roles.kernel.internal.transaction.constants import _EN_DEBUG_FIX_RE

prompt = '读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。'
full_msg = '''读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。

[Benchmark Tool Contract]
This is a deterministic tool-calling matrix run. Follow the contract strictly.
Required tool groups: one of [read_file] ; one of [glob, repo_rg].
Forbidden tools: execute_command, search_replace, edit_file.
Tool call count must be between 1 and 3.
Final response must include exact substrings: helpers.
These substrings are mandatory in your final response.'''

lowered = full_msg.lower()
match = _EN_DEBUG_FIX_RE.search(lowered)
print(f'_EN_DEBUG_FIX_RE match: {match}')
if match:
    print(f'  Matched: {match.group()!r} at pos {match.start()}')
    print(f'  Context: {lowered[max(0,match.start()-20):match.end()+20]!r}')
