import sys

sys.path.insert(0, r'c:\Users\dains\Documents\GitLab\polaris\src\backend')
from polaris.cells.roles.kernel.internal.transaction.constants import DEBUG_AND_FIX_CN_MARKERS

# Check each marker for false positive risk: can it appear as a sub-word?
# Test against typical read/exploration sentences
test_phrases = [
    '不确定位置', '查找文件', '读取内容', '异常情况', '定位问题',
    '排查一下', '解决思路', '报错了', '排查原因', '确定方案',
    '无法确定位置', '不清楚在哪', '定位文件'
]
print('=== Potential false positive markers ===')
for phrase in test_phrases:
    for marker in DEBUG_AND_FIX_CN_MARKERS:
        if marker in phrase and phrase != marker:
            print(f'  Phrase "{phrase}" contains marker "{marker}" -> FALSE POSITIVE RISK')

print()
print('=== All DEBUG_AND_FIX_CN_MARKERS ===')
for m in DEBUG_AND_FIX_CN_MARKERS:
    print(f'  {m!r}')
