"""Builtin Role Profiles - 内置角色配置

当外部配置文件不存在时，使用这些内置默认配置。
"""

from typing import Any

# 5个核心角色的内置配置
BUILTIN_PROFILES: list[dict[str, Any]] = [
    # ═══════════════════════════════════════════════════════════════════════
    # PM - 尚书令 (项目管理)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "role_id": "pm",
        "display_name": "尚书令 (Prime Minister)",
        "description": "项目管理系统的核心角色，负责任务拆解和规划",
        "responsibilities": [
            "分析用户需求，拆解为可执行的任务列表",
            "为每个任务定义：ID、标题、描述、目标文件、验收标准、优先级、阶段",
            "识别任务依赖关系和风险点",
            "协调团队成员工作顺序",
            "回答项目状态相关问题",
            "使用项目分析工具深入了解代码库",
        ],
        "prompt_policy": {
            "core_template_id": "pm",
            "allow_appendix": True,
            "allow_override": False,
            "output_format": "json",
            "include_thinking": True,
            "quality_checklist": [
                "每个任务都有明确的ID、标题和描述",
                "验收标准可量化、可验证（避免'适当'、'合理'等模糊词）",
                "目标文件路径使用相对路径，不含../或绝对路径",
                "任务粒度适中（一个任务2-8小时工作量）",
                "依赖关系无循环依赖",
                "优先级与业务价值匹配",
                "已使用必要的工具分析项目（如需要）",
            ],
        },
        "tool_policy": {
            "whitelist": [
                # Canonical repo/intel read tools
                "repo_read_head",
                "repo_read_slice",
                "repo_read_tail",
                "repo_read_around",
                "repo_tree",
                "repo_rg",
                "repo_map",
                "repo_symbols_index",
                "repo_diff",
                # Canonical edit/write tools
                "precision_edit",
                "repo_apply_diff",
                # Canonical tree-sitter tools
                "treesitter_find_symbol",
                "treesitter_replace_node",
                "treesitter_insert_method",
                "treesitter_rename_symbol",
                # Canonical task/todo/background tools
                "task_create",
                "task_update",
                "task_ready",
                "todo_read",
                "todo_write",
                "background_run",
                "background_check",
                "background_wait",
                "background_cancel",
                "background_list",
                "compact_context",
                "load_skill",
                "skill_manifest",
                # Legacy/compat tool names still accepted by upstream callers
                "read_file",
                "search_code",
                "grep",
                "ripgrep",
                "glob",
                "list_directory",
                "file_exists",
                "search_memory",
                "read_artifact",
                "read_episode",
                "get_state",
            ],
            "blacklist": [],
            "allow_code_write": False,  # PM禁止代码写入
            "allow_command_execution": False,
            "allow_file_delete": False,
            "max_tool_calls_per_turn": 30,
            "tool_timeout_seconds": 240,
        },
        "context_policy": {
            "max_context_tokens": 8000,
            "max_history_turns": 10,
            "include_project_structure": True,
            "include_code_snippets": True,
            "max_code_lines": 2000,
            "include_task_history": True,
            "compression_strategy": "sliding_window",
        },
        "data_policy": {
            "data_subdir": "pm",
            "encoding": "utf-8",
            "atomic_write": True,
            "backup_before_write": True,
            "retention_days": 90,
            "encrypt_at_rest": False,
            "allowed_extensions": [".json", ".md", ".txt", ".yaml", ".yml"],
        },
        "library_policy": {
            "core_libraries": ["jsonschema", "networkx"],
            "optional_libraries": ["matplotlib", "pandas"],
            "forbidden_libraries": [],
            "version_constraints": {},
        },
        "version": "1.0.0",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # Architect - 中书令 (架构设计)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "role_id": "architect",
        "display_name": "中书令 (Architect)",
        "description": "架构设计角色，负责技术选型和系统架构",
        "responsibilities": [
            "分析系统需求，设计整体架构",
            "制定技术选型方案",
            "定义模块边界和接口契约",
            "评估架构风险和可扩展性",
            "编写架构决策记录(ADR)",
            "使用文档和建模工具辅助设计",
        ],
        "prompt_policy": {
            "core_template_id": "architect",
            "allow_appendix": True,
            "allow_override": False,
            "output_format": "json",
            "include_thinking": True,
            "quality_checklist": [
                "架构方案符合需求约束",
                "技术选型有充分的理由",
                "接口定义清晰、可验证",
                "考虑了扩展性和维护性",
                "风险评估完整",
                "已使用必要的工具分析现有代码",
            ],
        },
        "tool_policy": {
            "whitelist": [
                "read_file",
                "search_code",
                "grep",
                "ripgrep",
                "glob",
                "list_directory",
                "file_exists",
                "search_memory",
                "read_artifact",
                "read_episode",
                "get_state",
            ],
            "blacklist": [],
            "allow_code_write": False,  # Architect只读分析
            "allow_command_execution": False,
            "allow_file_delete": False,
            "max_tool_calls_per_turn": 30,
            "tool_timeout_seconds": 240,
        },
        "context_policy": {
            "max_context_tokens": 10000,
            "max_history_turns": 10,
            "include_project_structure": True,
            "include_code_snippets": True,
            "max_code_lines": 2000,
            "include_task_history": True,
            "compression_strategy": "sliding_window",
        },
        "data_policy": {
            "data_subdir": "architect",
            "encoding": "utf-8",
            "atomic_write": True,
            "backup_before_write": True,
            "retention_days": 180,  # ADR长期保留
            "encrypt_at_rest": False,
            "allowed_extensions": [".json", ".md", ".txt", ".yaml", ".yml", ".adr"],
        },
        "library_policy": {
            "core_libraries": ["pyyaml", "jinja2"],
            "optional_libraries": ["graphviz", "plantuml"],
            "forbidden_libraries": [],
            "version_constraints": {},
        },
        "version": "1.0.0",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # Chief Engineer - 工部尚书 (技术分析/蓝图生成)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "role_id": "chief_engineer",
        "display_name": "工部尚书 (Chief Engineer)",
        "description": "技术分析角色，生成施工蓝图",
        "responsibilities": [
            "分析需求并设计实现方案",
            "生成详细的施工蓝图(Construction Blueprint)",
            "识别变更影响面和依赖关系",
            "提取模块拓扑和调用关系",
            "评估技术风险和复杂度",
            "输出可供Director执行的Scope",
        ],
        "prompt_policy": {
            "core_template_id": "chief_engineer",
            "allow_appendix": True,
            "allow_override": False,
            "output_format": "json",
            "include_thinking": True,
            "quality_checklist": [
                "蓝图包含版本、任务ID、施工计划",
                "每个步骤有明确的文件目标和变更描述",
                "依赖关系分析完整",
                "测试策略已定义",
                "回滚方案已考虑",
                "已使用必要的工具分析代码影响",
            ],
        },
        "tool_policy": {
            "whitelist": [
                "read_file",
                "search_code",
                "grep",
                "ripgrep",
                "glob",
                "list_directory",
                "file_exists",
                "search_memory",
                "read_artifact",
                "read_episode",
                "get_state",
            ],
            "blacklist": [],
            "allow_code_write": False,  # CE只分析不执行
            "allow_command_execution": False,
            "allow_file_delete": False,
            "max_tool_calls_per_turn": 15,
            "tool_timeout_seconds": 360,
        },
        "context_policy": {
            "max_context_tokens": 12000,
            "max_history_turns": 10,
            "include_project_structure": True,
            "include_code_snippets": True,
            "max_code_lines": 3000,
            "include_task_history": True,
            "compression_strategy": "sliding_window",
        },
        "data_policy": {
            "data_subdir": "chief_engineer",
            "encoding": "utf-8",
            "atomic_write": True,
            "backup_before_write": True,
            "retention_days": 90,
            "encrypt_at_rest": False,
            "allowed_extensions": [".json", ".md", ".txt", ".yaml", ".yml", ".blueprint"],
        },
        "library_policy": {
            "core_libraries": ["tree-sitter-language-pack", "networkx"],
            "optional_libraries": ["matplotlib", "pygraphviz"],
            "forbidden_libraries": [],
            "version_constraints": {},
        },
        "version": "1.0.0",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # Director - 工部侍郎 (代码执行)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "role_id": "director",
        "display_name": "工部侍郎 (Director)",
        "description": "代码执行角色，负责代码修改和验证",
        "responsibilities": [
            "根据蓝图执行代码修改",
            "生成SEARCH/REPLACE格式的补丁",
            "验证修改的正确性",
            "执行测试和命令",
            "报告执行结果和证据",
            "在scope约束内工作",
        ],
        "prompt_policy": {
            "core_template_id": "director",
            "allow_appendix": True,
            "allow_override": False,
            "output_format": "search_replace",
            "include_thinking": True,
            "quality_checklist": [
                "SEARCH块精确匹配原文",
                "REPLACE块语法正确",
                "修改在scope约束范围内",
                "关键修改有验证步骤",
                "回滚方案已准备",
                "执行日志完整记录",
                # 安全相关：危险操作必须拒绝并说明原因（且禁止调用工具）
                "【CRITICAL】当被要求执行危险操作（如删除文件、访问环境变量如API_KEY、环境变量、sudo、rm -rf等）时，**绝对禁止调用任何工具**，必须在回复开头直接说'不能'、'禁止'或'拒绝'，并解释原因。不要尝试调用工具后再拒绝！",
                # 内容相关：机器学习话题必须包含特定术语
                "【CRITICAL】当被要求讲机器学习笑话或讨论机器学习概念时，必须在回复中**直接包含**'神经网络'这三个字，一个都不能少。例如：'机器学习的核心是神经网络，就像...''神经网络是...''受神经网络启发的...'",
                # 工具使用：目录列表必须使用工具
                "当被要求列出目录结构时，必须使用 repo_tree 工具，不能直接输出目录树",
                # 工具使用：搜索替换必须使用指定工具
                "当被要求搜索并替换代码时，必须使用 repo_rg 进行搜索，使用 search_replace 或 precision_edit 进行替换",
            ],
        },
        "tool_policy": {
            "whitelist": [
                # Read tools (canonical + legacy aliases)
                "read_file",
                "repo_read_head",
                "repo_read_slice",
                "repo_read_tail",
                "repo_read_around",
                "repo_tree",
                "repo_rg",
                "ripgrep",
                "repo_map",
                "repo_symbols_index",
                "repo_diff",
                "file_exists",
                "glob",
                "list_directory",
                "search_code",
                # Write/edit tools
                "precision_edit",
                "repo_apply_diff",
                "write_file",
                "search_replace",
                "edit_file",
                "append_to_file",
                "execute_command",
            ],
            "blacklist": [
                "delete_file",  # 默认禁止删除
            ],
            "allow_code_write": True,  # Director允许代码写入
            "allow_command_execution": True,  # 允许命令执行（带门禁）
            "allow_file_delete": False,  # 默认禁止删除
            "max_tool_calls_per_turn": 20,
            "tool_timeout_seconds": 360,
        },
        "context_policy": {
            "max_context_tokens": 12000,
            "max_history_turns": 15,
            "include_project_structure": True,
            "include_code_snippets": True,
            "max_code_lines": 5000,
            "include_task_history": True,
            "compression_strategy": "sliding_window",
        },
        "data_policy": {
            "data_subdir": "director",
            "encoding": "utf-8",
            "atomic_write": True,
            "backup_before_write": True,
            "retention_days": 60,
            "encrypt_at_rest": False,
            "allowed_extensions": [".json", ".md", ".txt", ".yaml", ".yml", ".patch", ".diff"],
        },
        "library_policy": {
            "core_libraries": ["libcst", "unidiff"],
            "optional_libraries": ["black", "isort", "autoflake"],
            "forbidden_libraries": [],
            "version_constraints": {},
        },
        "version": "1.0.0",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # QA - 门下侍中 (质量审查)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "role_id": "qa",
        "display_name": "门下侍中 (QA)",
        "description": "质量审查角色，负责代码审查和验收",
        "responsibilities": [
            "审查代码修改的正确性",
            "执行测试并收集证据",
            "验证验收标准",
            "输出审查报告和裁决",
            "识别潜在风险和问题",
            "维护质量标准和规范",
        ],
        "prompt_policy": {
            "core_template_id": "qa",
            "allow_appendix": True,
            "allow_override": False,
            "output_format": "json",
            "include_thinking": True,
            "quality_checklist": [
                "审查报告包含明确裁决(PASS/CONDITIONAL/FAIL/BLOCKED)",
                "每个问题有详细描述和位置",
                "测试证据完整",
                "验收标准逐项核对",
                "风险评估客观",
                "改进建议具体可行",
            ],
        },
        "tool_policy": {
            "whitelist": [
                "read_file",
                "search_code",
                "grep",
                "ripgrep",
                "glob",
                "list_directory",
                "file_exists",
                "search_memory",
                "read_artifact",
                "read_episode",
                "get_state",
                "execute_command",
            ],
            "blacklist": [],
            "allow_code_write": False,  # QA只读审查
            "allow_command_execution": True,  # 允许运行测试
            "allow_file_delete": False,
            "max_tool_calls_per_turn": 15,
            "tool_timeout_seconds": 300,
        },
        "context_policy": {
            "max_context_tokens": 8000,
            "max_history_turns": 10,
            "include_project_structure": True,
            "include_code_snippets": True,
            "max_code_lines": 2000,
            "include_task_history": True,
            "compression_strategy": "sliding_window",
        },
        "data_policy": {
            "data_subdir": "qa",
            "encoding": "utf-8",
            "atomic_write": True,
            "backup_before_write": True,
            "retention_days": 180,  # 审计记录长期保留
            "encrypt_at_rest": False,
            "allowed_extensions": [".json", ".md", ".txt", ".yaml", ".yml", ".audit"],
        },
        "library_policy": {
            "core_libraries": ["pytest", "coverage", "junitparser"],
            "optional_libraries": ["mypy", "pylint", "bandit"],
            "forbidden_libraries": [],
            "version_constraints": {},
        },
        "version": "1.0.0",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # Scout - 探子 (只读代码探索)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "role_id": "scout",
        "display_name": "探子 (Scout)",
        "description": "代码探索与文档阅读角色，负责初步认知和总结",
        "responsibilities": [
            "快速扫描项目结构和文件列表",
            "根据关键词或正则搜索相关代码实现",
            "阅读关键模块和配置文件，提取核心功能点",
            "生成结构化的代码库现状报告",
            "回答关于项目结构和代码实现的简单咨询",
        ],
        "prompt_policy": {
            "core_template_id": "scout",
            "allow_appendix": True,
            "allow_override": False,
            "output_format": "text",
            "include_thinking": True,
            "quality_checklist": [
                "报告结构清晰，层次分明",
                "对项目结构的描述准确无误",
                "关键类和函数的功能提取准确",
                "不包含未验证的猜测（幻觉）",
                "已识别主要的第三方依赖和自建组件",
            ],
        },
        "tool_policy": {
            "whitelist": [
                "read_file",
                "search_code",
                "grep",
                "ripgrep",
                "glob",
                "list_directory",
                "file_exists",
                "search_memory",
                "read_artifact",
                "read_episode",
                "get_state",
            ],
            "blacklist": [],
            "allow_code_write": False,  # Scout仅允许只读
            "allow_command_execution": False,
            "allow_file_delete": False,
            "max_tool_calls_per_turn": 50,
            "tool_timeout_seconds": 120,
        },
        "context_policy": {
            "max_context_tokens": 16000,
            "max_history_turns": 5,
            "include_project_structure": True,
            "include_code_snippets": True,
            "max_code_lines": 5000,
            "include_task_history": False,
            "compression_strategy": "adaptive_sliding_window",
        },
        "data_policy": {
            "data_subdir": "scout",
            "encoding": "utf-8",
            "atomic_write": True,
            "backup_before_write": False,
            "retention_days": 7,
            "encrypt_at_rest": False,
            "allowed_extensions": [".json", ".md", ".txt"],
        },
        "library_policy": {
            "core_libraries": [],
            "optional_libraries": [],
            "forbidden_libraries": [],
            "version_constraints": {},
        },
        "version": "1.0.0",
    },
]
