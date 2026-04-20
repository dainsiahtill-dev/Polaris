from pathlib import Path
import re

# 1. Add Conflict Override Protocol
pt_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel/internal/prompt_templates.py')
pt_content = pt_path.read_text(encoding='utf-8')

new_rule = '5. 【指令冲突降级协议】：当用户或上下文中出现互相矛盾的指令（例如同一工具既被要求调用又被禁止调用）时，必须以系统安全红线和禁止策略为最高优先级。若无法调和，立即宣告工程阻断并向用户报告冲突原因。\\n'.strip()

if '指令冲突降级协议' not in pt_content:
    pt_content = pt_content.replace('不得伪造完成状态。\\n\"\"\".strip()', '不得伪造完成状态。\\n' + new_rule + '\\n\"\"\".strip()')
    pt_path.write_text(pt_content, encoding='utf-8')


# 2. Modify Working Memory Contract
pb_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel/internal/prompt_builder.py')
pb_content = pb_path.read_text(encoding='utf-8')

old_working_memory = '''    WORKING_MEMORY_CONTRACT_GUIDE = """
【工作记忆契约 / Working Memory Contract — 多回合执行专用】
在每个 Turn 结束时，若任务尚未完全解决，你**必须**在回复末尾输出结构化的 <SESSION_PATCH> 块。
该块不会被用户看到，仅供系统更新工作记忆，用于指导后续回合的执行方向。

**输出位置**：回复的最末尾（在所有工具调用结果之后）。
**格式**：严格遵循以下 JSON Schema：

<SESSION_PATCH>
{
    "task_progress": "exploring | investigating | implementing | verifying | done",
    "confidence": "hypothesis | likely | confirmed",
    "error_summary": "本回合发现的错误摘要（如有）",
    "suspected_files": ["本回合怀疑的问题文件路径"],
    "patched_files": ["本回合已修复的文件路径"],
    "verified_results": ["本回合验证通过的结论"],
    "pending_files": ["待进一步验证的文件路径"],
    "action_taken": "本回合采取的关键行动（1-2句）",
    "superseded": false,
    "key_file_snapshots": {"文件路径": "本回合该文件的快照指纹（可选）"}
}
</SESSION_PATCH>

**字段说明**：
- 	ask_progress：宏观进度推进（exploring→investigating→implementing→verifying→done），推进后才填新值，不变则沿用旧值。
- confidence：置信度等级，决定发现物的优先级：
  - hypothesis（初始值）：探索阶段的猜测，置信度最低，可被 likely 覆盖
  - likely：有一定证据的推断，置信度中等，可被 confirmed 覆盖
  - confirmed：经测试/验证确认的事实，置信度最高，覆盖一切低等级结论
- superseded：true 时系统将当前 patch 中的字段标记为废弃，后续续写 prompt 不再包含这些发现物（用于推翻旧假设）
- error_summary：仅填本次新发现的错误，已有结论勿重复填入。
- suspected_files：	ask_progress 仍为 exploring/investigating 时追加；进入 implementing 后停止追加。
- patched_files：	ask_progress 进入 implementing 后填写。
- pending_files：需要下一回合继续验证的假设。
- 
emove_keys：当发现之前的怀疑是伪线索时，用此字段撤销（如 {"suspected_files": ["fake.py"]}）。

**置信度升级示例**：
- Turn 1: "confidence": "hypothesis" → 初步猜测 auth.py 有问题
- Turn 2: "confidence": "likely" → 发现 auth.py 中 token 刷新逻辑确实有缺陷
- Turn 3: "confidence": "confirmed" → 单元测试验证了缺陷存在，准备修复

**推翻旧假设示例**：
- Turn 1 猜测 suspected_files: ["db.py"]，confidence: "likely"
- Turn 2 测试发现 db.py 完全正常，superseded: true，suspected_files: []
- 系统自动过滤 db.py，后续续写不再提及
""".strip()'''

new_working_memory = '''    WORKING_MEMORY_CONTRACT_GUIDE = """
【工作记忆契约 / Working Memory Contract — 多回合执行专用】
在每个 Turn 结束时，若任务尚未完全解决，你**必须**调用 update_session_state 工具来更新工作记忆。
该工具不会向用户展示文本，而是供底层系统推进状态机，用于指导后续回合的执行方向。

**工具调用规则**：
- 你必须使用正式的原生工具调用 update_session_state。
- 绝不要在回复正文中直接手写 <SESSION_PATCH> XML/JSON 块！所有的状态更新必须走标准的 API 传参！
- 	ask_progress：宏观进度推进（exploring→investigating→implementing→verifying→done），推进后才填新值。
- confidence：置信度等级，决定发现物的优先级：hypothesis -> likely -> confirmed。
- superseded：传递 true 时系统将当前状态标记为废弃，后续续写不再包含这些发现物（用于推翻旧假设）。
- 每次只填本次新发现的 error_summary 或最新验证通过的 erified_results，勿重复填入旧数据。
""".strip()'''

pb_content = pb_content.replace(old_working_memory, new_working_memory)
pb_path.write_text(pb_content, encoding='utf-8')

print("Applied conflict override protocol and replaced SESSION_PATCH with update_session_state instructions")
