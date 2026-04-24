"""Replay Suite Generator for Attention Runtime Evaluation.

This module provides programmatic generation of attention runtime evaluation
suites for replay/long-session testing scenarios.

The generator creates synthetic but semantically coherent conversation
sequences that test various attention runtime behaviors:

1. Session Length Tests:
   - Short sessions (3-5 turns)
   - Medium sessions (10-20 turns)
   - Long sessions (30+ turns)

2. Topic Dynamics:
   - Multi-topic switching
   - Topic interleaving
   - Return to previous topic

3. Follow-up Lifecycle:
   - Pending follow-up creation
   - Confirmation/Denial responses
   - Pause and resume
   - Redirect to different action

4. Attention Regression:
   - Latest intent override
   - High-priority response capture
   - Multiple attention roots
"""

from __future__ import annotations

from .evaluation import AttentionRuntimeEvalSuite, AttentionRuntimeQualityCase

# =============================================================================
# Conversation Building Blocks
# =============================================================================


def _user(content: str) -> dict[str, str]:
    """Create a user message."""
    return {"role": "user", "content": content}


def _assistant(content: str) -> dict[str, str]:
    """Create an assistant message."""
    return {"role": "assistant", "content": content}


def _tool(content: str) -> dict[str, str]:
    """Create a tool result message."""
    return {"role": "tool", "content": content}


def _format_conversation(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Ensure all messages have proper role and content fields."""
    formatted = []
    for msg in messages:
        role = str(msg.get("role", "")).strip().lower()
        content = str(msg.get("content", ""))
        if role in ("user", "assistant", "tool"):
            formatted.append({"role": role, "content": content})
    return formatted


# =============================================================================
# Generator Functions
# =============================================================================


def generate_long_session_suite(
    num_turns: int,
    theme: str,
    *,
    base_case_id: str = "generated_long_session",
) -> AttentionRuntimeEvalSuite:
    """Generate a long session evaluation suite.

    Args:
        num_turns: Number of conversation turns (minimum 10)
        theme: Theme for the session (e.g., "implementation", "bugfix", "refactor")
        base_case_id: Base identifier for generated cases

    Returns:
        AttentionRuntimeEvalSuite with generated cases

    Raises:
        ValueError: If num_turns < 10
    """
    if num_turns < 10:
        raise ValueError(f"num_turns must be >= 10 for long session, got {num_turns}")

    cases: list[AttentionRuntimeQualityCase] = []

    if theme == "implementation":
        cases.append(_generate_implementation_session(num_turns, base_case_id))
    elif theme == "bugfix":
        cases.append(_generate_bugfix_session(num_turns, base_case_id))
    elif theme == "refactor":
        cases.append(_generate_refactor_session(num_turns, base_case_id))
    elif theme == "code_review":
        cases.append(_generate_code_review_session(num_turns, base_case_id))
    else:
        cases.append(_generate_generic_session(num_turns, base_case_id))

    return AttentionRuntimeEvalSuite(
        version=1,
        suite_id=f"long_session_{theme}_{num_turns}",
        description=f"Long session evaluation for {theme} with {num_turns} turns",
        cases=tuple(cases),
    )


def generate_multi_topic_suite(
    num_topics: int = 3,
    base_case_id: str = "generated_multi_topic",
) -> AttentionRuntimeEvalSuite:
    """Generate a multi-topic switching evaluation suite.

    Args:
        num_topics: Number of topics to interleave (2-5 recommended)
        base_case_id: Base identifier for generated cases

    Returns:
        AttentionRuntimeEvalSuite with multi-topic cases
    """
    cases: list[AttentionRuntimeQualityCase] = []

    # Case 1: Simple topic switching
    cases.append(
        _generate_topic_switching_case(
            topics=["排序算法", "天气查询", "文件处理"],
            turns_per_topic=3,
            case_id=f"{base_case_id}_simple_switch",
        )
    )

    # Case 2: Deep dive then switch
    cases.append(
        _generate_deep_dive_switch_case(
            primary_topic="用户认证",
            secondary_topics=["日志配置", "性能优化"],
            primary_depth=8,
            secondary_depth=2,
            case_id=f"{base_case_id}_deep_then_switch",
        )
    )

    # Case 3: Rapid interleaving
    cases.append(
        _generate_interleaved_case(
            topics=["登录功能", "注册功能", "密码找回"],
            interleaves=4,
            case_id=f"{base_case_id}_rapid_interleave",
        )
    )

    # Case 4: Return to previous topic
    cases.append(
        _generate_return_to_previous_case(
            case_id=f"{base_case_id}_return",
        )
    )

    # Case 5: Many topics (stress test)
    if num_topics > 3:
        many_topics = [
            "用户管理",
            "权限控制",
            "日志记录",
            "缓存配置",
            "数据库优化",
            "API设计",
            "前端组件",
            "测试覆盖",
        ]
        cases.append(
            _generate_topic_switching_case(
                topics=many_topics[:num_topics],
                turns_per_topic=2,
                case_id=f"{base_case_id}_many_topics",
            )
        )

    return AttentionRuntimeEvalSuite(
        version=1,
        suite_id=f"multi_topic_{num_topics}",
        description=f"Multi-topic switching evaluation with {num_topics} topics",
        cases=tuple(cases),
    )


def generate_followup_lifecycle_suite(
    base_case_id: str = "generated_followup_lifecycle",
) -> AttentionRuntimeEvalSuite:
    """Generate a pending follow-up lifecycle evaluation suite.

    Tests the complete lifecycle of pending follow-ups:
    - Creation on assistant question
    - Resolution via user response
    - Pause and resume
    - Redirect to different action
    - Denial and alternative

    Returns:
        AttentionRuntimeEvalSuite with follow-up lifecycle cases
    """
    cases: list[AttentionRuntimeQualityCase] = [
        # Follow-up with confirmation
        _generate_followup_confirmed_case(f"{base_case_id}_confirmed"),
        # Follow-up with denial
        _generate_followup_denied_case(f"{base_case_id}_denied"),
        # Follow-up with pause
        _generate_followup_paused_case(f"{base_case_id}_paused"),
        # Follow-up with redirect
        _generate_followup_redirected_case(f"{base_case_id}_redirected"),
        # Multiple sequential follow-ups
        _generate_sequential_followups_case(f"{base_case_id}_sequential"),
        # Nested follow-ups (follow-up after follow-up)
        _generate_nested_followup_case(f"{base_case_id}_nested"),
        # Follow-up with complex confirmation
        _generate_complex_followup_case(f"{base_case_id}_complex"),
        # Follow-up timeout scenario
        _generate_followup_timeout_case(f"{base_case_id}_timeout"),
    ]

    return AttentionRuntimeEvalSuite(
        version=1,
        suite_id="followup_lifecycle",
        description="Pending follow-up lifecycle evaluation suite",
        cases=tuple(cases),
    )


# =============================================================================
# Internal Case Generators
# =============================================================================


def _generate_implementation_session(
    num_turns: int,
    base_case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate an implementation-focused long session."""
    conversation: list[dict[str, str]] = [
        _user("帮我创建一个数据处理模块"),
        _assistant("好的，数据处理模块需要包含哪些功能？"),
        _user("需要数据读取、清洗、转换功能"),
        _assistant("好的，我来创建数据处理模块。"),
        _tool("创建 data_processor.py"),
        _assistant("数据处理模块基础框架已创建。"),
        _user("添加数据验证功能"),
        _assistant("好的，我来添加数据验证功能。"),
        _tool("更新 data_processor.py"),
        _assistant("数据验证功能已添加。"),
        _user("添加错误处理"),
        _assistant("好的，我来添加错误处理。"),
        _tool("更新 data_processor.py"),
        _assistant("错误处理已添加。"),
        _user("添加日志记录"),
        _assistant("好的，我来添加日志记录。"),
        _tool("更新 data_processor.py"),
        _assistant("日志记录已添加。"),
        _user("添加性能优化"),
        _assistant("好的，我来添加性能优化。"),
        _tool("更新 data_processor.py"),
        _assistant("性能优化已应用。"),
        _user("添加单元测试"),
        _assistant("好的，我来添加单元测试。"),
        _tool("创建 test_data_processor.py"),
        _assistant("单元测试已创建。"),
        _user("运行测试"),
        _assistant("好的，运行测试中..."),
        _tool("测试运行完成"),
        _assistant("所有测试通过。"),
        _user("生成文档"),
        _assistant("好的，我来生成文档。"),
        _tool("创建 README.md"),
        _assistant("文档已生成。"),
    ]

    # Extend conversation if more turns requested
    extensions = [
        (
            _user("添加类型注解"),
            _assistant("好的，我来添加类型注解。"),
            _tool("更新 data_processor.py"),
            _assistant("类型注解已添加。"),
        ),
        (
            _user("添加更多测试用例"),
            _assistant("好的，我来添加更多测试。"),
            _tool("更新 test_data_processor.py"),
            _assistant("更多测试用例已添加。"),
        ),
        (
            _user("优化代码结构"),
            _assistant("好的，我来优化代码结构。"),
            _tool("重构 data_processor.py"),
            _assistant("代码结构已优化。"),
        ),
    ]

    while len(conversation) // 4 < num_turns // 4 and len(extensions) > 0:
        for ext in extensions:
            conversation.extend(ext)
            if len(conversation) // 4 >= num_turns // 4:
                break

    conversation.extend([_user("完成了吗"), _assistant("是的，数据处理模块已完成。")])

    return AttentionRuntimeQualityCase(
        case_id=f"{base_case_id}_impl_{num_turns}",
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_bugfix_session(
    num_turns: int,
    base_case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a bug fixing-focused long session."""
    conversation: list[dict[str, str]] = [
        _user("帮我修复一个登录bug"),
        _assistant("请描述一下bug的具体表现？"),
        _user("登录时提示超时错误"),
        _assistant("让我检查一下登录模块的代码。"),
        _tool("读取 login.py"),
        _assistant("发现问题了，连接超时设置太短。请问需要我修复吗？"),
        _user("需要"),
        _assistant("好的，我来修复超时问题。"),
        _tool("修复 login.py"),
        _assistant("超时问题已修复。"),
        _user("还有其他问题吗"),
        _assistant("让我检查一下..."),
        _tool("检查完成"),
        _assistant("发现还有一个会话过期问题。请问需要我修复吗？"),
        _user("需要"),
        _assistant("好的，我来修复会话过期问题。"),
        _tool("修复 login.py"),
        _assistant("会话过期问题已修复。"),
        _user("再检查一下"),
        _assistant("好的，我来再次检查..."),
        _tool("检查完成"),
        _assistant("目前没有发现其他问题。"),
        _user("好，测试一下"),
        _assistant("好的，运行测试中..."),
        _tool("测试通过"),
        _assistant("所有测试通过。"),
    ]

    conversation.extend([_user("完成了吗"), _assistant("是的，登录bug已修复。")])

    return AttentionRuntimeQualityCase(
        case_id=f"{base_case_id}_bugfix_{num_turns}",
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_refactor_session(
    num_turns: int,
    base_case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a refactoring-focused long session."""
    conversation: list[dict[str, str]] = [
        _user("帮我重构订单模块"),
        _assistant("好的，订单模块重构的目标是什么？"),
        _user("提高代码可读性和可维护性"),
        _assistant("好的，我来分析订单模块的代码结构。"),
        _tool("读取 order.py"),
        _assistant("发现订单模块包含多个职责的代码，需要拆分。请问需要我重构吗？"),
        _user("需要"),
        _assistant("好的，我来开始重构。"),
        _tool("重构 order.py"),
        _assistant("正在重构..."),
        _user("添加单元测试"),
        _assistant("好的，我来添加单元测试。"),
        _tool("创建 test_order.py"),
        _assistant("单元测试已创建。"),
        _user("运行测试"),
        _assistant("好的，运行测试中..."),
        _tool("测试通过"),
        _assistant("所有测试通过。"),
        _user("生成重构报告"),
        _assistant("好的，我来生成重构报告。"),
        _tool("创建 REFACTOR_REPORT.md"),
        _assistant("重构报告已生成。"),
    ]

    conversation.extend([_user("完成了吗"), _assistant("是的，订单模块重构已完成。")])

    return AttentionRuntimeQualityCase(
        case_id=f"{base_case_id}_refactor_{num_turns}",
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_code_review_session(
    num_turns: int,
    base_case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a code review-focused long session."""
    conversation: list[dict[str, str]] = [
        _user("帮我审查用户模块的代码"),
        _assistant("好的，我来审查用户模块的代码。"),
        _tool("读取 user.py"),
        _assistant("代码审查中..."),
        _assistant("发现以下问题：1) 函数过长 2) 缺少错误处理 3) 有重复代码。需要我帮你改进吗？"),
        _user("需要"),
        _assistant("好的，我来改进代码。"),
        _tool("更新 user.py"),
        _assistant("代码已改进。"),
        _user("还有什么问题吗"),
        _assistant("让我继续审查..."),
        _tool("继续审查"),
        _assistant("发现命名不规范的问题。请问需要我改进吗？"),
        _user("需要"),
        _assistant("好的，我来改进命名。"),
        _tool("更新 user.py"),
        _assistant("命名已改进。"),
        _user("再检查一下"),
        _assistant("好的，让我继续检查..."),
        _tool("检查完成"),
        _assistant("代码审查完成，没有发现其他问题。"),
        _user("好"),
    ]

    conversation.extend([_user("完成了吗"), _assistant("是的，代码审查和改进已完成。")])

    return AttentionRuntimeQualityCase(
        case_id=f"{base_case_id}_review_{num_turns}",
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_generic_session(
    num_turns: int,
    base_case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a generic long session with mixed content."""
    conversation: list[dict[str, str]] = [
        _user("帮我完成项目初始化"),
        _assistant("好的，项目初始化需要哪些配置？"),
        _user("基本的项目结构和依赖配置"),
        _assistant("好的，我来创建项目结构。"),
        _tool("创建项目结构"),
        _assistant("项目结构已创建。"),
        _user("添加开发依赖"),
        _assistant("好的，我来添加开发依赖。"),
        _tool("更新配置文件"),
        _assistant("开发依赖已添加。"),
        _user("配置CI/CD"),
        _assistant("好的，我来配置CI/CD。"),
        _tool("创建 .github/workflows"),
        _assistant("CI/CD已配置。"),
        _user("添加代码规范检查"),
        _assistant("好的，我来添加代码规范检查。"),
        _tool("配置 lint"),
        _assistant("代码规范检查已配置。"),
        _user("设置测试覆盖率要求"),
        _assistant("好的，我来设置测试覆盖率要求。"),
        _tool("配置覆盖率"),
        _assistant("测试覆盖率要求已设置。"),
        _user("完成了吗"),
        _assistant("是的，项目初始化已完成。"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=f"{base_case_id}_generic_{num_turns}",
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_topic_switching_case(
    topics: list[str],
    turns_per_topic: int,
    case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a topic switching case."""
    conversation: list[dict[str, str]] = []
    last_topic = ""

    for i, topic in enumerate(topics):
        # Switch to new topic
        if last_topic and i > 0:
            conversation.extend(
                [
                    _user(f"等下，先处理{topics[0]}的问题"),
                    _assistant(f"好的，{topics[0]}的问题处理中..."),
                    _tool("处理完成"),
                    _assistant(f"{topics[0]}的问题已处理。"),
                    _user(f"继续刚才的{topic}"),
                ]
            )

        conversation.extend(
            [
                _user(f"帮我处理{topic}"),
                _assistant(f"好的，{topic}处理中..."),
                _tool(f"处理 {topic}"),
            ]
        )

        # Add follow-up question
        if turns_per_topic > 2:
            conversation.extend(
                [
                    _assistant(f"{topic}处理完成。需要我做其他调整吗？"),
                    _user("需要"),
                ]
            )

        last_topic = topic

    conversation.append(_assistant("所有任务已完成。"))

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="所有任务已完成。"
        if not conversation or conversation[-1].get("content") != "所有任务已完成。"
        else "",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=len(topics),
        expect_seal_blocked=False,
    )


def _generate_deep_dive_switch_case(
    primary_topic: str,
    secondary_topics: list[str],
    primary_depth: int,
    secondary_depth: int,
    case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a case with deep primary topic then switch."""
    conversation: list[dict[str, str]] = [
        _user(f"帮我实现{primary_topic}功能"),
        _assistant(f"好的，{primary_topic}需要哪些功能？"),
    ]

    # Deep dive on primary topic
    features = ["基础框架", "核心逻辑", "错误处理", "性能优化", "安全检查", "测试覆盖", "文档生成", "部署配置"]
    for _i, feature in enumerate(features[:primary_depth]):
        conversation.extend(
            [
                _user(f"添加{feature}"),
                _assistant(f"好的，我来添加{feature}。"),
                _tool(f"实现 {feature}"),
                _assistant(f"{feature}已添加。"),
            ]
        )

    # Switch to secondary topic
    for sec_topic in secondary_topics[:secondary_depth]:
        conversation.extend(
            [
                _user(f"等下，帮我处理{sec_topic}"),
                _assistant(f"好的，{sec_topic}处理中..."),
                _tool(f"处理 {sec_topic}"),
                _assistant(f"{sec_topic}已完成。"),
                _user(f"继续{primary_topic}"),
                _assistant("好的，继续处理。"),
            ]
        )

    conversation.extend(
        [
            _user(f"继续完成{primary_topic}"),
            _assistant(f"{primary_topic}已完成。"),
        ]
    )

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent=f"继续完成{primary_topic}",
        expected_pending_followup_status="",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_interleaved_case(
    topics: list[str],
    interleaves: int,
    case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a case with rapid topic interleaving."""
    conversation: list[dict[str, str]] = []

    for _i in range(interleaves):
        for topic in topics:
            conversation.extend(
                [
                    _user(f"帮我处理{topic}"),
                    _assistant(f"{topic}处理中..."),
                    _tool(f"处理 {topic}"),
                    _assistant(f"{topic}已完成。"),
                ]
            )

    conversation.append(_user("完成了吗"))

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="",
        expected_attention_roots_count=len(topics),
        expect_seal_blocked=False,
    )


def _generate_return_to_previous_case(
    case_id: str,
) -> AttentionRuntimeQualityCase:
    """Generate a case that returns to a previous topic."""
    conversation: list[dict[str, str]] = [
        _user("帮我实现用户管理功能"),
        _assistant("好的，用户管理功能需要哪些功能？"),
        _user("增删改查"),
        _assistant("好的，我来创建用户管理模块。"),
        _tool("创建 user_manager.py"),
        _assistant("用户管理模块已创建。"),
        _user("帮我实现订单管理"),
        _assistant("好的，订单管理功能需要哪些功能？"),
        _user("订单创建和查询"),
        _assistant("好的，我来创建订单管理模块。"),
        _tool("创建 order_manager.py"),
        _assistant("订单管理模块已创建。"),
        _user("继续用户管理，添加导出功能"),
        _assistant("好的，我来添加导出功能到用户管理模块。"),
        _tool("更新 user_manager.py"),
        _assistant("导出功能已添加。"),
        _user("完成了吗"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="完成了吗",
        expected_pending_followup_status="",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


# Follow-up lifecycle cases


def _generate_followup_confirmed_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a follow-up confirmation case."""
    conversation = [
        _user("帮我创建API接口"),
        _assistant("好的，API接口需要支持哪些HTTP方法？"),
        _user("GET和POST"),
        _assistant("好的，需要我添加分页功能吗？"),
        _user("需要"),
        _assistant("好的，已添加分页功能。"),
        _tool("创建 api.py"),
        _assistant("API接口已创建，支持GET、POST和分页。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="好",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_followup_denied_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a follow-up denial case."""
    conversation = [
        _user("帮我实现文件上传"),
        _assistant("好的，需要我添加文件大小限制吗？"),
        _user("不需要"),
        _assistant("好的，已使用默认文件大小限制。"),
        _tool("创建 upload.py"),
        _assistant("文件上传功能已创建。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="好",
        expected_pending_followup_status="denied",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_followup_paused_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a follow-up pause case."""
    conversation = [
        _user("帮我配置缓存"),
        _assistant("好的，需要我配置Redis缓存吗？"),
        _user("等下，先别配置"),
        _assistant("好的，等你确认。"),
        _user("确认了，配置吧"),
        _assistant("好的，我来配置Redis缓存。"),
        _tool("配置 Redis"),
        _assistant("Redis缓存已配置。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="确认了，配置吧",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_followup_redirected_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a follow-up redirect case."""
    conversation = [
        _user("帮我实现邮件发送"),
        _assistant("好的，需要我使用SMTP协议吗？"),
        _user("改成使用API方式"),
        _assistant("好的，我来修改为API方式。"),
        _tool("更新 mail.py"),
        _assistant("已修改为API方式发送邮件。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="改成使用API方式",
        expected_pending_followup_status="redirected",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_sequential_followups_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a case with multiple sequential follow-ups."""
    conversation = [
        _user("帮我创建数据处理管道"),
        _assistant("好的，数据处理管道需要哪些步骤？"),
        _user("读取、清洗、转换"),
        _assistant("好的，需要我添加数据验证吗？"),
        _user("需要"),
        _assistant("好的，已添加数据验证。"),
        _tool("创建 pipeline.py"),
        _assistant("数据处理管道已创建。"),
        _user("添加日志记录"),
        _assistant("好的，需要我同时添加错误追踪吗？"),
        _user("需要"),
        _assistant("好的，已添加错误追踪。"),
        _tool("更新 pipeline.py"),
        _assistant("日志记录和错误追踪已添加。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="好",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=3,
        expect_seal_blocked=False,
    )


def _generate_nested_followup_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a case with nested follow-ups."""
    conversation = [
        _user("帮我实现搜索功能"),
        _assistant("好的，搜索功能需要支持全文搜索吗？"),
        _user("需要"),
        _assistant("好的，已添加全文搜索。"),
        _tool("创建 search.py"),
        _assistant("搜索功能已创建。"),
        _user("添加过滤功能"),
        _assistant("好的，需要我添加多字段过滤吗？"),
        _user("需要"),
        _assistant("好的，已添加多字段过滤。"),
        _tool("更新 search.py"),
        _assistant("多字段过滤已添加。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="好",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_complex_followup_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a case with complex confirmation scenario."""
    conversation = [
        _user("帮我实现数据导出"),
        _assistant("好的，数据导出支持哪些格式？"),
        _user("JSON和CSV"),
        _assistant("好的，需要我添加压缩功能吗？"),
        _user("需要"),
        _assistant("好的，已添加压缩功能。"),
        _tool("创建 export.py"),
        _assistant("数据导出功能已创建。"),
        _user("添加导入功能"),
        _assistant("好的，导入功能需要支持哪些格式？"),
        _user("和导出一样"),
        _assistant("好的，已添加导入功能。"),
        _tool("更新 export.py"),
        _assistant("导入导出功能已完成。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="好",
        expected_pending_followup_status="confirmed",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


def _generate_followup_timeout_case(case_id: str) -> AttentionRuntimeQualityCase:
    """Generate a case simulating follow-up timeout/pause scenario."""
    conversation = [
        _user("帮我配置消息队列"),
        _assistant("好的，需要我使用RabbitMQ吗？"),
        _user("等一下，我需要先确认"),
        _assistant("好的，等你确认。"),
        _user("先配置Redis缓存"),
        _assistant("好的，我来配置Redis缓存。"),
        _tool("配置 Redis"),
        _assistant("Redis缓存已配置。"),
        _user("现在配置消息队列"),
        _assistant("好的，继续配置RabbitMQ。"),
        _tool("配置 RabbitMQ"),
        _assistant("RabbitMQ已配置。"),
        _user("好"),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent="现在配置消息队列",
        expected_pending_followup_status="",
        expected_attention_roots_count=2,
        expect_seal_blocked=False,
    )


# =============================================================================
# Combined Suite Generator
# =============================================================================


def generate_replay_benchmark_suite() -> AttentionRuntimeEvalSuite:
    """Generate a comprehensive replay benchmark suite.

    Combines all replay test scenarios into a single evaluation suite:
    - Long session tests (various themes)
    - Multi-topic switching tests
    - Follow-up lifecycle tests
    - Attention focus regression tests

    Returns:
        AttentionRuntimeEvalSuite with comprehensive coverage
    """
    all_cases: list[AttentionRuntimeQualityCase] = []

    # Add long session cases
    long_suite_10 = generate_long_session_suite(10, "implementation")
    long_suite_20 = generate_long_session_suite(20, "bugfix")
    long_suite_30 = generate_long_session_suite(30, "refactor")
    all_cases.extend(list(long_suite_10.cases))
    all_cases.extend(list(long_suite_20.cases))
    all_cases.extend(list(long_suite_30.cases))

    # Add multi-topic cases
    multi_topic_suite = generate_multi_topic_suite(3)
    all_cases.extend(list(multi_topic_suite.cases))

    # Add follow-up lifecycle cases
    followup_suite = generate_followup_lifecycle_suite()
    all_cases.extend(list(followup_suite.cases))

    return AttentionRuntimeEvalSuite(
        version=1,
        suite_id="replay_benchmark_v1",
        description="Comprehensive replay benchmark suite for attention runtime evaluation",
        cases=tuple(all_cases),
    )


# =============================================================================
# Direct Case Creation Utilities
# =============================================================================


def create_short_session_case(
    case_id: str,
    intent: str,
    followup_status: str = "",
) -> AttentionRuntimeQualityCase:
    """Create a simple short session test case.

    Args:
        case_id: Unique identifier for the case
        intent: Expected latest intent
        followup_status: Expected pending follow-up status

    Returns:
        AttentionRuntimeQualityCase with 3-turn conversation
    """
    conversation = [
        _user("请帮我处理一个任务"),
        _assistant("好的，请详细描述你的需求。"),
        _user(intent),
    ]

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent=intent,
        expected_pending_followup_status=followup_status,
        expected_attention_roots_count=2,
        expect_seal_blocked=followup_status == "pending",
    )


def create_multi_turn_case(
    case_id: str,
    turns: list[tuple[str, str, str]],
    final_intent: str,
    followup_status: str = "",
) -> AttentionRuntimeQualityCase:
    """Create a multi-turn test case from turn specifications.

    Args:
        case_id: Unique identifier for the case
        turns: List of (role, content, expected_pending_status) tuples
        final_intent: Expected latest intent
        followup_status: Expected final pending follow-up status

    Returns:
        AttentionRuntimeQualityCase with specified turns
    """
    conversation: list[dict[str, str]] = []
    attention_roots = 1

    for _i, (role, content, status_after) in enumerate(turns):
        if role == "user":
            conversation.append(_user(content))
        else:
            conversation.append(_assistant(content))

        # Track attention roots based on pending follow-ups
        if status_after in ("pending", "confirmed"):
            attention_roots += 1

    return AttentionRuntimeQualityCase(
        case_id=case_id,
        conversation=_format_conversation(conversation),
        expected_latest_intent=final_intent,
        expected_pending_followup_status=followup_status,
        expected_attention_roots_count=attention_roots,
        expect_seal_blocked=followup_status == "pending",
    )


__all__ = [
    "create_multi_turn_case",
    "create_short_session_case",
    "generate_followup_lifecycle_suite",
    "generate_long_session_suite",
    "generate_multi_topic_suite",
    "generate_replay_benchmark_suite",
]
