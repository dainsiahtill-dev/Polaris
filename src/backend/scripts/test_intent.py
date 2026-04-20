import sys
sys.path.insert(0, r'c:\Users\dains\Documents\GitLab\polaris\src\backend')
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import classify_intent_regex, requires_mutation_intent

# The L2 benchmark prompt + injected benchmark contract
full_msg = '''读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。

[Benchmark Tool Contract]
This is a deterministic tool-calling matrix run. Follow the contract strictly.
Required tool groups: one of [read_file] ; one of [glob, repo_rg].
Forbidden tools: execute_command, search_replace, edit_file.
Tool call count must be between 1 and 3.
Final response must include exact substrings: helpers.
These substrings are mandatory in your final response.'''

intent = classify_intent_regex(full_msg)
is_mut = requires_mutation_intent(full_msg)
print(f'Intent: {intent}')
print(f'requires_mutation: {is_mut}')
print()
# Now test pure user prompt
pure_msg = '读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。'
intent2 = classify_intent_regex(pure_msg)
is_mut2 = requires_mutation_intent(pure_msg)
print(f'[Pure user prompt] Intent: {intent2}')
print(f'[Pure user prompt] requires_mutation: {is_mut2}')
