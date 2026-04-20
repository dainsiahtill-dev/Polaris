#!/usr/bin/env python3
"""DEBUG 可折叠功能演示脚本

展示如何使用 CollapsibleDebugRenderer 在 CLI 中实现 DEBUG 消息折叠。

Usage:
    python demo_collapsible_debug.py
"""

from __future__ import annotations

from polaris.delivery.cli.debug_renderer import CollapsibleDebugRenderer


def main() -> None:
    print("=" * 70)
    print("CLI DEBUG 可折叠功能演示")
    print("=" * 70)
    print()

    # 创建渲染器
    renderer = CollapsibleDebugRenderer()

    # 模拟多个 DEBUG 事件
    debug_events = [
        {
            "category": "fs",
            "label": "read",
            "source": "kernelone",
            "tags": {"file": "test.py"},
            "payload": {"path": "/workspace/test.py", "size": 1024, "encoding": "utf-8"},
        },
        {
            "category": "llm",
            "label": "request",
            "source": "openai",
            "tags": {"model": "gpt-4"},
            "payload": {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.7,
            },
        },
        {
            "category": "tool",
            "label": "execute",
            "source": "director",
            "tags": {"tool": "bash", "status": "success"},
            "payload": {"command": "ls -la", "exit_code": 0, "output": "..."},
        },
    ]

    print("1. 添加 DEBUG 消息（默认折叠）")
    print("-" * 50)
    for i, event in enumerate(debug_events, 1):
        renderer.print_debug(event, json_render="pretty")
        print()

    print(f"DEBUG 消息数量: {renderer.get_debug_count()}")
    print()

    print("2. 展开所有 DEBUG")
    print("-" * 50)
    count = renderer.expand_all_debug()
    print(f"展开了 {count} 条消息")
    renderer.print_all_debug()
    print()

    print("3. 折叠所有 DEBUG")
    print("-" * 50)
    count = renderer.collapse_all_debug()
    print(f"折叠了 {count} 条消息")
    renderer.print_all_debug()
    print()

    print("4. 切换 DEBUG 可见性")
    print("-" * 50)
    is_expanded = renderer.toggle_debug()
    print(f"切换后状态: {'展开' if is_expanded else '折叠'}")
    renderer.print_all_debug()
    print()

    print("5. 快捷键帮助")
    print("-" * 50)
    renderer.show_help()
    print()

    print("=" * 70)
    print("演示结束")
    print("=" * 70)


if __name__ == "__main__":
    main()
