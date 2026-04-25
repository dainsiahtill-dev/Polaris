import sys

sys.path.insert(0, r'c:\Users\dains\Documents\GitLab\polaris\src\backend')

import importlib

import polaris.cells.roles.kernel.internal.transaction.intent_classifier as m

importlib.reload(m)

from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    classify_intent_regex,
    requires_mutation_intent,
)

tests = [
    # (prompt, expected_intent, expected_requires_mutation)
    ('读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。', 'UNKNOWN', False),
    ('定位 config.py 中的配置错误并修复它', 'DEBUG_AND_FIX', True),
    ('排查服务器报错原因', 'DEBUG_AND_FIX', True),
    ('解决这个 bug', 'DEBUG_AND_FIX', True),
    ('异常了，帮我修一下', 'DEBUG_AND_FIX', True),
    ('修改 config.py 里的配置', 'STRONG_MUTATION', True),
    ('读取 README.md', 'UNKNOWN', False),
    ('无法确定位置，请帮我查找', 'UNKNOWN', False),
]

print('=== Intent Classifier Tests ===')
all_pass = True
for prompt, exp_intent, exp_mut in tests:
    intent = classify_intent_regex(prompt)
    is_mut = requires_mutation_intent(prompt)
    ok = (intent == exp_intent) and (is_mut == exp_mut)
    status = 'PASS' if ok else 'FAIL'
    if not ok:
        all_pass = False
    print(f'  [{status}] intent={intent} (exp={exp_intent}) mut={is_mut} (exp={exp_mut})')
    print(f'         prompt: {prompt[:60]}')
print()
print('ALL PASS' if all_pass else 'SOME TESTS FAILED')
