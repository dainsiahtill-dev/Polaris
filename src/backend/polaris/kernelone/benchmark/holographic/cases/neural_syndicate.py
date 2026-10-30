"""Neural-syndicate multi-agent benchmark executors (TC-NS-001..004).

Mailbox throughput, message routing, payload serialization, and
TTL/hop-limit accuracy.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from polaris.kernelone.benchmark.holographic.stats import _perf_ms
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import summarize_samples
from polaris.kernelone.multi_agent.bus_port import create_in_memory_bus_port
from polaris.kernelone.multi_agent.neural_syndicate.base_agent import BaseAgent
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    Intent,
    Performative,
)
from polaris.kernelone.multi_agent.neural_syndicate.router import MessageRouter


async def _exec_tc_ns_002(case: HolographicCase) -> dict[str, float]:
    router = MessageRouter(hop_limit=10)
    capabilities = [AgentCapability(name="exec", intents=[Intent.EXECUTE_TASK])]
    for index in range(20):
        await router.register_agent(f"agent-{index}", capabilities)

    unicast_samples_us: list[float] = []
    broadcast_samples_us: list[float] = []
    iterations = max(1000, case.min_samples)

    for index in range(iterations):
        unicast_msg = AgentMessage(
            sender="bench",
            receiver=f"agent-{index % 20}",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            payload={"i": index},
        )
        begin = time.perf_counter_ns()
        await router.route(unicast_msg)
        unicast_samples_us.append((time.perf_counter_ns() - begin) / 1000.0)

        broadcast_msg = AgentMessage(
            sender="bench",
            receiver="",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
            payload={"i": index},
        )
        begin = time.perf_counter_ns()
        await router.route(broadcast_msg)
        broadcast_samples_us.append((time.perf_counter_ns() - begin) / 1000.0)

    unicast_stats = summarize_samples(unicast_samples_us, warmup_rounds=case.warmup_rounds)
    broadcast_stats = summarize_samples(broadcast_samples_us, warmup_rounds=case.warmup_rounds)
    ratio = broadcast_stats.p99 / unicast_stats.p99 if unicast_stats.p99 > 0 else 0.0
    return {
        "unicast_p50_us": unicast_stats.p50,
        "unicast_p90_us": unicast_stats.p90,
        "unicast_p99_us": unicast_stats.p99,
        "broadcast_p50_us": broadcast_stats.p50,
        "broadcast_p90_us": broadcast_stats.p90,
        "broadcast_p99_us": broadcast_stats.p99,
        "broadcast_unicast_p99_ratio": ratio,
    }


async def _exec_tc_ns_003(case: HolographicCase) -> dict[str, float]:
    sizes = [100, 1_000, 10_000, 100_000, 1_000_000]
    serialize_ms: list[float] = []
    deserialize_ms: list[float] = []
    payload_100kb_roundtrip_ms: list[float] = []
    total_bytes = 0
    for size in sizes:
        payload_text = "x" * size
        msg = AgentMessage(
            sender="bench",
            receiver="agent-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            payload={"blob": payload_text},
        )
        loops = 400 if size >= 100_000 else 1000
        for _ in range(loops):
            serialize_begin = time.perf_counter_ns()
            blob = msg.model_dump_json()
            serialize_ms.append(_perf_ms(serialize_begin))
            total_bytes += len(blob.encode("utf-8"))

            deserialize_begin = time.perf_counter_ns()
            AgentMessage.model_validate_json(blob)
            deserialize_ms.append(_perf_ms(deserialize_begin))

            if size == 100_000:
                roundtrip_ms = (time.perf_counter_ns() - serialize_begin) / 1_000_000.0
                payload_100kb_roundtrip_ms.append(roundtrip_ms)

    ser_stats = summarize_samples(serialize_ms, warmup_rounds=case.warmup_rounds)
    de_stats = summarize_samples(deserialize_ms, warmup_rounds=case.warmup_rounds)
    payload_100kb_stats = summarize_samples(payload_100kb_roundtrip_ms, warmup_rounds=case.warmup_rounds)
    total_ser_s = sum(serialize_ms) / 1000.0
    total_de_s = sum(deserialize_ms) / 1000.0
    ser_mb_s = (total_bytes / (1024 * 1024)) / total_ser_s if total_ser_s > 0 else 0.0
    de_mb_s = (total_bytes / (1024 * 1024)) / total_de_s if total_de_s > 0 else 0.0
    return {
        "payload_100kb_p99_ms": payload_100kb_stats.p99,
        "serialize_p99_ms": ser_stats.p99,
        "deserialize_p99_ms": de_stats.p99,
        "serialize_mb_s": ser_mb_s,
        "deserialize_mb_s": de_mb_s,
    }


async def _exec_tc_ns_004(case: HolographicCase) -> dict[str, float]:
    total = max(1000, case.min_samples)
    ttl_ok = 0
    hop_ok = 0
    ttl_drop = 0
    for _ in range(total):
        message = AgentMessage(
            sender="a",
            receiver="",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=3,
            hop_count=0,
        )
        forwarded = message
        local_ttl_ok = True
        local_hop_ok = True
        for hop in range(1, 6):
            if forwarded.is_expired:
                ttl_drop += 1
                break
            forwarded = forwarded.with_forward(next_hop=f"n{hop}")
            if forwarded.hop_count != hop:
                local_hop_ok = False
            if forwarded.remaining_hops != max(0, forwarded.ttl - forwarded.hop_count):
                local_ttl_ok = False
        if local_ttl_ok:
            ttl_ok += 1
        if local_hop_ok:
            hop_ok += 1
    return {
        "ttl_decrement_accuracy_percent": (ttl_ok / total) * 100.0,
        "hop_increment_accuracy_percent": (hop_ok / total) * 100.0,
        "ttl_zero_drop_percent": (ttl_drop / total) * 100.0,
    }


class _MailboxBenchmarkAgent(BaseAgent):
    """Lightweight BaseAgent implementation for mailbox throughput benchmarks."""

    def __init__(self, *args: Any, process_delay_s: float = 0.0005, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._process_delay_s = max(0.0, float(process_delay_s))
        self.latencies_ms: list[float] = []

    @property
    def agent_type(self) -> str:
        return "benchmark_mailbox_agent"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability(name="benchmark_consumer", intents=[Intent.EXECUTE_TASK])]

    async def _handle_message(self, message: AgentMessage) -> AgentMessage | None:
        emit_ns = int(message.metadata.get("emit_ns", 0) or 0)
        if emit_ns > 0:
            self.latencies_ms.append((time.perf_counter_ns() - emit_ns) / 1_000_000.0)
        if self._process_delay_s > 0:
            await asyncio.sleep(self._process_delay_s)
        return None

    def enqueue(self, message: AgentMessage) -> bool:
        envelope = self._message_to_envelope(message)
        return self._bus_port.publish(envelope)


async def _exec_tc_ns_001(case: HolographicCase) -> dict[str, float]:
    agent = _MailboxBenchmarkAgent(
        agent_id="bench-agent",
        bus_port=create_in_memory_bus_port(),
        mailbox_size=4096,
        mailbox_poll_interval=0.001,
        process_delay_s=0.0,
    )
    total_messages = max(1000, case.min_samples * 10)
    published = 0
    backlog_peak = 0
    production_done = asyncio.Event()
    start_ns = time.perf_counter_ns()

    async def consumer() -> None:
        nonlocal backlog_peak
        while not production_done.is_set() or not agent._mailbox.empty():
            try:
                message = await asyncio.wait_for(
                    agent._mailbox.get(),
                    timeout=0.01,
                )
            except TimeoutError:
                continue

            await agent._process_message(message)
            backlog_peak = max(
                backlog_peak,
                agent._mailbox.qsize(),
            )

    consumer_task = asyncio.create_task(consumer())
    try:
        for index in range(total_messages):
            message = AgentMessage(
                sender="producer",
                receiver=agent.agent_id,
                performative=Performative.REQUEST,
                intent=Intent.EXECUTE_TASK,
                payload={"index": index},
                metadata={"emit_ns": time.perf_counter_ns()},
            )
            try:
                agent._mailbox.put_nowait(message)
            except asyncio.QueueFull:
                break
            published += 1
            backlog_peak = max(
                backlog_peak,
                agent._mailbox.qsize(),
            )

        production_done.set()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            stats = agent.get_stats()
            backlog_peak = max(backlog_peak, agent._mailbox.qsize())
            if int(stats.get("messages_processed", 0)) >= published:
                break
            await asyncio.sleep(0.001)

        try:
            await asyncio.wait_for(consumer_task, timeout=2.0)
        except TimeoutError:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task

        elapsed_s = max((time.perf_counter_ns() - start_ns) / 1_000_000_000.0, 1e-9)
        processed = int(agent.get_stats().get("messages_processed", 0))
        latency_stats = summarize_samples(agent.latencies_ms, warmup_rounds=case.warmup_rounds)
        return {
            "throughput_msg_s": processed / elapsed_s,
            "e2e_p50_ms": latency_stats.p50,
            "e2e_p90_ms": latency_stats.p90,
            "e2e_p99_ms": latency_stats.p99,
            "mailbox_backlog_peak": float(backlog_peak),
        }
    finally:
        production_done.set()
        if not consumer_task.done():
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
