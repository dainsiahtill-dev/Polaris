"""测试流式 thinking 解析修复

测试内容：
1. reasoning_content 原生字段和 <thinking> 标签同时存在时，不应重复
2. 已有原生 reasoning 时，content 中的 <thinking> 标签应被正确剥离
3. 内容不应重复输出
"""


# 导入需要测试的模块
import sys

sys.path.insert(0, 'src/backend')

from polaris.kernelone.llm.providers import THINKING_PREFIX
from polaris.kernelone.llm.providers.stream_thinking_parser import ChunkKind, StreamThinkingParser


def test_stream_thinking_parser_basic():
    """测试基本的 thinking 标签解析"""
    parser = StreamThinkingParser()

    # 测试完整的 thinking 标签
    results = parser.feed_sync("Hello <think>thinking content</think> world", final=True)

    print("Test 1: Basic thinking tag parsing")
    print("  Input: 'Hello <think>thinking content</think> world'")
    print(f"  Results: {results}")

    # 应该返回 3 个部分: Hello (TEXT), thinking content (THINKING), world (TEXT)
    text_chunks = [c for c in results if c[0] == ChunkKind.TEXT]
    thinking_chunks = [c for c in results if c[0] == ChunkKind.THINKING]
    assert len(text_chunks) == 2, f"Expected 2 TEXT results, got {len(text_chunks)}"
    assert len(thinking_chunks) == 1, f"Expected 1 THINKING result, got {len(thinking_chunks)}"
    assert text_chunks[0] == (ChunkKind.TEXT, "Hello "), f"Expected (ChunkKind.TEXT, 'Hello '), got {text_chunks[0]}"
    assert thinking_chunks[0] == (ChunkKind.THINKING, "thinking content"), f"Expected (ChunkKind.THINKING, 'thinking content'), got {thinking_chunks[0]}"
    assert text_chunks[1] == (ChunkKind.TEXT, " world"), f"Expected (ChunkKind.TEXT, ' world'), got {text_chunks[1]}"
    print("  ✓ PASSED\n")


def test_stream_thinking_parser_across_tokens():
    """测试跨 token 的 thinking 标签"""
    parser = StreamThinkingParser()

    # 模拟标签被分割到多个 token（注意：标签本身不被分割，内容被分割）
    tokens = ["Hello ", "<think>", "thinking", " content", "</think>", " world"]
    all_results = []
    for i, token in enumerate(tokens):
        results = parser.feed_sync(token, final=(i == len(tokens) - 1))
        all_results.extend(results)

    print("Test 2: Across-token thinking tag")
    print(f"  Tokens: {tokens}")
    print(f"  Results: {all_results}")

    # 检查最终的 TEXT 是否包含 "Hello " 和 " world"
    content_parts = [text for kind, text in all_results if kind == ChunkKind.TEXT]
    thinking_parts = [text for kind, text in all_results if kind == ChunkKind.THINKING]

    full_content = "".join(content_parts)
    full_thinking = "".join(thinking_parts)

    print(f"  Content: '{full_content}'")
    print(f"  Thinking: '{full_thinking}'")

    assert "Hello " in full_content, f"Expected 'Hello ' in content, got '{full_content}'"
    assert " world" in full_content, f"Expected ' world' in content, got '{full_content}'"
    assert "thinking content" in full_thinking, f"Expected 'thinking content' in thinking, got '{full_thinking}'"
    print("  ✓ PASSED\n")


def test_no_duplicate_reasoning():
    """测试 reasoning 不重复

    模拟场景：API 返回原生 reasoning_content，同时 content 中包含 <thinking> 标签
    修复后的行为：当已有原生 reasoning 时，应跳过 content 中的 <thinking> 标签
    """
    print("Test 3: No duplicate reasoning (with fix)")

    # 模拟 provider 输出序列
    # 1. 原生 reasoning
    # 2. content 中的 <thinking> 标签（应被忽略，因为已有原生 reasoning）
    # 3. 普通 content

    provider_outputs = [
        f"{THINKING_PREFIX}Native reasoning content",
        "<think>This should be ignored</think>",
        "Actual content",
    ]

    # 模拟修复后的 provider 处理逻辑
    # 当已有原生 reasoning 时，使用 think parser 但只保留 content 部分
    parser = StreamThinkingParser()
    reasoning_chunks = []
    content_chunks = []
    has_native_reasoning = False

    for token in provider_outputs:
        if token.startswith(THINKING_PREFIX):
            reasoning_text = token[len(THINKING_PREFIX):]
            reasoning_chunks.append(reasoning_text)
            has_native_reasoning = True
        else:
            # 修复后的逻辑：当已有原生 reasoning 时，
            # 使用 think parser 但只保留 TEXT，丢弃 THINKING
            if has_native_reasoning:
                for kind, text in parser.feed_sync(token, final=True):
                    if kind == ChunkKind.TEXT:
                        content_chunks.append(text)
            else:
                # 没有原生 reasoning 时，正常解析
                for kind, text in parser.feed_sync(token, final=True):
                    if kind == ChunkKind.THINKING:
                        reasoning_chunks.append(text)
                    else:
                        content_chunks.append(text)

    print(f"  Reasoning chunks: {reasoning_chunks}")
    print(f"  Content chunks: {content_chunks}")

    # 验证
    full_reasoning = "".join(reasoning_chunks)
    full_content = "".join(content_chunks)

    assert "Native reasoning content" in full_reasoning, f"Expected native reasoning, got '{full_reasoning}'"
    assert "This should be ignored" not in full_reasoning, f"Got unexpected reasoning from content: '{full_reasoning}'"
    assert "Actual content" in full_content, f"Expected 'Actual content', got '{full_content}'"
    assert "<think>" not in full_content, f"Thinking tag leaked into content: '{full_content}'"
    print("  ✓ PASSED\n")


def test_kimi_provider_logic():
    """测试 Kimi provider 的处理逻辑"""
    print("Test 4: Kimi provider logic simulation")

    # 模拟 Kimi API 的 delta 数据
    # 注意：这里的 has_native_reasoning 是按 delta 检测的，不是全局的
    # 也就是说，只有当前 delta 有原生 reasoning 时，才跳过 content 中的 thinking
    deltas = [
        {"reasoning_content": "First reasoning"},  # 原生 reasoning
        {"content": "<think>This should be skipped</think>Some content"},  # 没有原生 reasoning，需要解析 thinking
        {"content": "More content"},  # 没有原生 reasoning
        {"reasoning_content": "More reasoning"},  # 原生 reasoning
    ]

    def flatten_text(value):
        if isinstance(value, str):
            return [value] if value else []
        return []

    def extract_delta_content_parts(content):
        if isinstance(content, str) and content:
            return [("content", content)]
        return []

    think_parser = StreamThinkingParser()
    out = []
    # 全局标记：一旦检测到过原生 reasoning，后续 content 中的 thinking 都跳过
    has_seen_native_reasoning = False

    for delta in deltas:
        # 首先检查原生的 reasoning 字段
        has_native_reasoning_in_this_delta = False
        for key in ("reasoning_content", "reasoning", "thinking"):
            for text in flatten_text(delta.get(key)):
                if text:
                    out.append(f"[REASONING]{text}")
                    has_native_reasoning_in_this_delta = True
                    has_seen_native_reasoning = True

        # 处理 content 字段
        for part_kind, text in extract_delta_content_parts(delta.get("content")):
            if not text:
                continue
            if part_kind == "reasoning":
                if not has_seen_native_reasoning:
                    out.append(f"[REASONING]{text}")
                continue

            # 如果已检测到过原生 reasoning，跳过 content 中的 <thinking> 标签
            if has_seen_native_reasoning:
                for think_kind, parsed_text in think_parser.feed_sync(text, final=True):
                    if not parsed_text:
                        continue
                    if think_kind == ChunkKind.TEXT:
                        out.append(f"[CONTENT]{parsed_text}")
                continue

            # 没有原生 reasoning 时，使用 think parser 解析
            for think_kind, parsed_text in think_parser.feed_sync(text, final=True):
                if not parsed_text:
                    continue
                if think_kind == ChunkKind.THINKING:
                    out.append(f"[REASONING]{parsed_text}")
                else:
                    out.append(f"[CONTENT]{parsed_text}")

    print(f"  Output: {out}")

    # 验证
    reasoning_parts = [s for s in out if s.startswith("[REASONING]")]
    content_parts = [s for s in out if s.startswith("[CONTENT]")]

    print(f"  Reasoning: {reasoning_parts}")
    print(f"  Content: {content_parts}")

    # 应该只有 2 个 reasoning，而不是 3 个
    assert len(reasoning_parts) == 2, f"Expected 2 reasoning parts, got {len(reasoning_parts)}: {reasoning_parts}"

    # content 中不应该有 thinking 标签
    full_content = "".join(content_parts)
    assert "<think>" not in full_content, f"Thinking tag leaked: {full_content}"

    # content 应该包含 "Some content" 和 "More content"
    assert "Some content" in full_content, f"Missing 'Some content': {full_content}"
    assert "More content" in full_content, f"Missing 'More content': {full_content}"

    print("  ✓ PASSED\n")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Stream Thinking Parser Fixes")
    print("=" * 60 + "\n")

    test_stream_thinking_parser_basic()
    test_stream_thinking_parser_across_tokens()
    test_no_duplicate_reasoning()
    test_kimi_provider_logic()

    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)
