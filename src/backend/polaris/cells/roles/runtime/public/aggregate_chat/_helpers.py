"""Shared leaf helpers for the aggregate_chat package."""

from __future__ import annotations

from typing import cast

from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatMessageV1,
)


def _aggregate_objective_from_messages(messages: tuple[AggregateChatMessageV1, ...]) -> str:
    user_messages = [message.content for message in messages if message.role.lower() == "user"]
    if user_messages:
        return cast("str", (user_messages[-1]))
    return cast("str", (messages[-1].content))
