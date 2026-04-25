import sys

sys.path.insert(0, r'c:\Users\dains\Documents\GitLab\polaris\src\backend')
from polaris.cells.roles.kernel.internal.transaction.constants import DEBUG_AND_FIX_CN_MARKERS

prompt = '读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。'
hits_cn = [m for m in DEBUG_AND_FIX_CN_MARKERS if m in prompt]
print(f'Matched DEBUG_AND_FIX_CN_MARKERS in pure prompt: {hits_cn}')

# Now check full injected msg from the logs
full_msg = '''读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。

[Benchmark Tool Contract]
This is a deterministic tool-calling matrix run. Follow the contract strictly.
Required tool groups: one of [read_file] ; one of [glob, repo_rg].
Forbidden tools: execute_command, search_replace, edit_file.
Tool call count must be between 1 and 3.
Final response must include exact substrings: helpers.
These substrings are mandatory in your final response.'''
hits_cn2 = [m for m in DEBUG_AND_FIX_CN_MARKERS if m in full_msg]
print(f'Matched DEBUG_AND_FIX_CN_MARKERS in full msg: {hits_cn2}')

# Check ALL signals
from polaris.cells.roles.kernel.internal.transaction.constants import (
    ANALYSIS_ONLY_SIGNALS,
    STRONG_MUTATION_CN_MARKERS,
    WEAK_MUTATION_CN_MARKERS,
)

print(f'STRONG_MUTATION matches: {[m for m in STRONG_MUTATION_CN_MARKERS if m in prompt]}')
print(f'WEAK_MUTATION matches: {[m for m in WEAK_MUTATION_CN_MARKERS if m in prompt]}')
print(f'ANALYSIS_ONLY matches: {[m for m in ANALYSIS_ONLY_SIGNALS if m in prompt]}')
