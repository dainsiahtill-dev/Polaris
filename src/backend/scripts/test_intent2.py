import sys
sys.path.insert(0, r'c:\Users\dains\Documents\GitLab\polaris\src\backend')
from polaris.cells.roles.kernel.internal.transaction.constants import DEBUG_AND_FIX_EN_MARKERS

prompt = '读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。'
lowered = prompt.lower()
hits = [m for m in DEBUG_AND_FIX_EN_MARKERS if m in lowered]
print(f'Matched DEBUG_AND_FIX_EN_MARKERS: {hits}')

# Also test benchmark injection
full_msg = prompt + '\n\n[Benchmark Tool Contract]\nRequired tool groups: one of [read_file]...'
lowered2 = full_msg.lower()
hits2 = [m for m in DEBUG_AND_FIX_EN_MARKERS if m in lowered2]
print(f'Matched DEBUG_AND_FIX_EN_MARKERS in full msg: {hits2}')
