"""Streaming / robust-parser benchmark executors (TC-NW-001..004, TC-ER-001..004).

Event-stream serialization, backpressure, stream tool-call accumulation,
multi-channel fan-out, and robust JSON / response-normalizer parsing.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from polaris.kernelone.benchmark.holographic.stats import _perf_ms, _serialized_json
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import summarize_samples
from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.llm.engine.normalizer import ResponseNormalizer
from polaris.kernelone.llm.engine.stream.backpressure import BackpressureBuffer
from polaris.kernelone.llm.engine.stream.event_streamer import (
    EventStreamer,
    SerializationFormat,
)
from polaris.kernelone.llm.engine.stream.executor import StreamExecutor
from polaris.kernelone.llm.response_parser import LLMResponseParser
from polaris.kernelone.llm.robust_parser.core import RobustParser
from polaris.kernelone.stream.backpressure_buffer import AsyncBackpressureBuffer
from pydantic import BaseModel, ValidationError


async def _exec_tc_er_003(case: HolographicCase) -> dict[str, float]:
    samples = [
        'Here is the JSON: {"key": "value"}',
        'JSON output: {"name": "alice", "count": 3}',
        '```json\n{"foo": 1, "bar": 2}\n```\nAdditional text',
        'prefix text {"z": true} suffix',
    ]
    total = max(200, case.min_samples)
    success = 0
    for index in range(total):
        text = samples[index % len(samples)]
        extracted = ResponseNormalizer.extract_json_object(text)
        if extracted is not None:
            success += 1
    coverage = (success / total) * 100.0
    return {
        "prefix_clean_coverage_percent": coverage,
        "extract_success_percent": coverage,
    }


async def _exec_tc_nw_001(case: HolographicCase) -> dict[str, float]:
    events: list[AIStreamEvent] = []
    for index in range(100):
        events.append(AIStreamEvent.chunk_event(f"chunk-{index}"))
    for index in range(20):
        events.append(AIStreamEvent.reasoning_event(f"reasoning-{index}"))
    for index in range(10):
        events.append(AIStreamEvent.tool_call_event({"tool": "search", "arguments": {"q": index}}))
    events.append(AIStreamEvent.complete({"ok": True}))

    loops = max(50, min(case.min_samples, 200))
    json_event_latencies_ms: list[float] = []
    msgpack_event_latencies_ms: list[float] = []
    json_streamer = EventStreamer(serialization_format=SerializationFormat.JSON)
    msgpack_streamer = EventStreamer(serialization_format=SerializationFormat.MSGPACK)

    json_started = time.perf_counter_ns()
    for _ in range(loops):
        for event in events:
            begin = time.perf_counter_ns()
            _ = json_streamer.serialize_event(event)
            json_event_latencies_ms.append(_perf_ms(begin))
    json_total_ms = _perf_ms(json_started)

    msgpack_total_ms = 0.0
    msgpack_available = True
    msgpack_started = time.perf_counter_ns()
    try:
        for _ in range(loops):
            for event in events:
                begin = time.perf_counter_ns()
                _ = msgpack_streamer.serialize_event(event)
                msgpack_event_latencies_ms.append(_perf_ms(begin))
        msgpack_total_ms = _perf_ms(msgpack_started)
    except RuntimeError:
        msgpack_available = False
        msgpack_event_latencies_ms = list(json_event_latencies_ms)
        msgpack_total_ms = json_total_ms

    json_stats = summarize_samples(json_event_latencies_ms, warmup_rounds=case.warmup_rounds)
    msgpack_stats = summarize_samples(msgpack_event_latencies_ms, warmup_rounds=case.warmup_rounds)
    total_events = loops * len(events)
    return {
        "serialization_p99_ms": json_stats.p99,
        "json_events_s": total_events / max(json_total_ms / 1000.0, 1e-9),
        "msgpack_events_s": total_events / max(msgpack_total_ms / 1000.0, 1e-9),
        "msgpack_p99_ms": msgpack_stats.p99,
        "msgpack_available": 1.0 if msgpack_available else 0.0,
    }


async def _run_backpressure_scenario(
    buffer: BackpressureBuffer | AsyncBackpressureBuffer,
    *,
    producer_count: int,
    items_per_producer: int,
    consumer_delay_s: float,
) -> tuple[float, float]:
    wait_samples_ms: list[float] = []
    total_items = producer_count * items_per_producer
    consumed = 0
    finished = asyncio.Event()
    start_ns = time.perf_counter_ns()

    async def producer(producer_id: int) -> None:
        for item_index in range(items_per_producer):
            chunk = f"{producer_id}:{item_index}"
            started = time.perf_counter_ns()
            await buffer.feed(chunk)
            wait_samples_ms.append(_perf_ms(started))

    async def consumer() -> None:
        nonlocal consumed
        while not finished.is_set() or buffer.size > 0:
            await asyncio.sleep(consumer_delay_s)
            if isinstance(buffer, AsyncBackpressureBuffer):
                drained = await buffer.drain()
            else:
                drained = buffer.drain()
            consumed += len(drained)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.gather(*(producer(producer_id) for producer_id in range(producer_count)))
    finished.set()
    await consumer_task

    elapsed_s = max((time.perf_counter_ns() - start_ns) / 1_000_000_000.0, 1e-9)
    throughput = consumed / elapsed_s
    wait_stats = summarize_samples(wait_samples_ms)
    _ = total_items
    return throughput, wait_stats.p99


async def _exec_tc_nw_002(case: HolographicCase) -> dict[str, float]:
    producer_count = 120
    items_per_producer = max(20, min(case.min_samples, 120))
    lock_buffer = BackpressureBuffer(max_size=100, backoff_seconds=0.0008)
    async_buffer = AsyncBackpressureBuffer(max_size=100, backoff_seconds=0.0008)

    lock_throughput, lock_wait_p99 = await _run_backpressure_scenario(
        lock_buffer,
        producer_count=producer_count,
        items_per_producer=items_per_producer,
        consumer_delay_s=0.0012,
    )
    async_throughput, async_wait_p99 = await _run_backpressure_scenario(
        async_buffer,
        producer_count=producer_count,
        items_per_producer=items_per_producer,
        consumer_delay_s=0.0012,
    )

    throughput_ratio = async_throughput / max(lock_throughput, 1e-9)
    wait_ratio = async_wait_p99 / max(lock_wait_p99, 1e-9)
    return {
        "threading_lock_throughput_events_s": lock_throughput,
        "async_queue_throughput_events_s": async_throughput,
        "async_queue_throughput_ratio": throughput_ratio,
        "threading_lock_wait_p99_ms": lock_wait_p99,
        "async_queue_wait_p99_ms": async_wait_p99,
        "async_queue_wait_p99_ratio": wait_ratio,
    }


async def _exec_tc_nw_003(case: HolographicCase) -> dict[str, float]:
    executor = StreamExecutor(workspace=".")
    with_accumulator_ms: list[float] = []
    without_accumulator_ms: list[float] = []
    sequence_ok = 0
    total = max(100, case.min_samples)

    for index in range(total):
        call_id = f"call-{index}"
        pending: dict[str, Any] = {}

        started = time.perf_counter_ns()
        tool_start = AIStreamEvent.tool_start_event("search", call_id=call_id)
        _ = tool_start
        first_delta = executor._accumulate_stream_tool_call(
            pending,
            {
                "tool": "search",
                "call_id": call_id,
                "arguments_text": '{"query":"',
                "index": 0,
            },
            ordinal=1,
            provider_type="mock",
        )
        second_delta = executor._accumulate_stream_tool_call(
            pending,
            {
                "tool": "search",
                "call_id": call_id,
                "arguments_text": f'term-{index}"}}',
                "arguments_complete": True,
                "index": 0,
            },
            ordinal=2,
            provider_type="mock",
        )
        finalized_payload: dict[str, Any] | None = second_delta or first_delta
        if finalized_payload is None:
            for accumulator in pending.values():
                finalized_payload = executor._finalize_stream_tool_call(accumulator)
                if finalized_payload is not None:
                    break
        tool_call_event = AIStreamEvent.tool_call_event(finalized_payload or {})
        tool_end_event = AIStreamEvent.tool_end_event("search", call_id=call_id, success=True)
        _ = tool_call_event, tool_end_event
        with_accumulator_ms.append(_perf_ms(started))

        started = time.perf_counter_ns()
        direct_payload = {"tool": "search", "call_id": call_id, "arguments": {"query": f"term-{index}"}}
        direct_call = AIStreamEvent.tool_call_event(direct_payload)
        direct_end = AIStreamEvent.tool_end_event("search", call_id=call_id, success=True)
        _ = direct_call, direct_end
        without_accumulator_ms.append(_perf_ms(started))

        if (
            isinstance(finalized_payload, dict)
            and finalized_payload.get("tool") == "search"
            and isinstance(finalized_payload.get("arguments"), dict)
            and finalized_payload.get("call_id") == call_id
        ):
            sequence_ok += 1

    with_stats = summarize_samples(with_accumulator_ms, warmup_rounds=case.warmup_rounds)
    without_stats = summarize_samples(without_accumulator_ms, warmup_rounds=case.warmup_rounds)
    return {
        "tool_e2e_p50_ms": with_stats.p50,
        "tool_e2e_p90_ms": with_stats.p90,
        "tool_e2e_p99_ms": with_stats.p99,
        "without_accumulator_p99_ms": without_stats.p99,
        "accumulator_overhead_percent": (
            ((with_stats.p99 - without_stats.p99) / without_stats.p99) * 100.0 if without_stats.p99 > 0 else 0.0
        ),
        "sequence_integrity_percent": (sequence_ok / total) * 100.0,
    }


async def _exec_tc_nw_004(case: HolographicCase) -> dict[str, float]:
    streamer = EventStreamer(serialization_format=SerializationFormat.JSON, max_queue_size=2048)
    thinking_latencies: list[float] = []
    tool_latencies: list[float] = []
    final_latencies: list[float] = []
    channel_counts = {"thinking": 0, "tool_log": 0, "final_answer": 0}
    total = max(1000, case.min_samples * 5)

    async def consume(channel: str, sink: list[float]) -> None:
        async for packet in streamer.subscribe(channel):
            decoded = packet.decode("utf-8")
            data_prefix = "data: "
            data_line = next((line for line in decoded.splitlines() if line.startswith(data_prefix)), "")
            if not data_line:
                continue
            payload = json.loads(data_line[len(data_prefix) :])
            emit_ns = int(payload.get("meta", {}).get("emit_ns", 0) or 0)
            if emit_ns > 0:
                sink.append((time.perf_counter_ns() - emit_ns) / 1_000_000.0)
            channel_counts[channel] += 1

    consumers = [
        asyncio.create_task(consume("thinking", thinking_latencies)),
        asyncio.create_task(consume("tool_log", tool_latencies)),
        asyncio.create_task(consume("final_answer", final_latencies)),
    ]

    try:
        for index in range(total):
            selector = index % 10
            if selector < 7:
                event = AIStreamEvent.reasoning_event("thinking", meta={"emit_ns": time.perf_counter_ns()})
                await streamer.publish(event, channel="thinking")
            elif selector < 9:
                event = AIStreamEvent.tool_call_event(
                    {"tool": "search", "arguments": {"q": index}},
                    meta={"emit_ns": time.perf_counter_ns()},
                )
                await streamer.publish(event, channel="tool_log")
            else:
                event = AIStreamEvent.chunk_event(f"answer-{index}", meta={"emit_ns": time.perf_counter_ns()})
                await streamer.publish(event, channel="final_answer")

        await asyncio.sleep(0.02)
    finally:
        await streamer.close()
        await asyncio.gather(*consumers, return_exceptions=True)

    thinking_stats = summarize_samples(thinking_latencies, warmup_rounds=case.warmup_rounds)
    tool_stats = summarize_samples(tool_latencies, warmup_rounds=case.warmup_rounds)
    final_stats = summarize_samples(final_latencies, warmup_rounds=case.warmup_rounds)
    channel_p99 = max(thinking_stats.p99, tool_stats.p99, final_stats.p99)
    starvation_channels = sum(1 for count in channel_counts.values() if count == 0)
    starvation_percent = (starvation_channels / max(len(channel_counts), 1)) * 100.0
    return {
        "channel_p99_ms": channel_p99,
        "thinking_p99_ms": thinking_stats.p99,
        "tool_log_p99_ms": tool_stats.p99,
        "final_answer_p99_ms": final_stats.p99,
        "starvation_percent": starvation_percent,
    }


class _EntropyPayload(BaseModel):
    key: str
    value: int


async def _exec_tc_er_001(case: HolographicCase) -> dict[str, float]:
    parser = RobustParser[_EntropyPayload](max_correction_turns=1, enable_correction=False, enable_fallback=True)
    total = max(500, case.min_samples * 5)
    normal_total = 0
    normal_success = 0
    markdown_total = 0
    markdown_success = 0
    truncated_total = 0
    truncated_success = 0

    for index in range(total):
        category = index % 5
        if category == 0:
            normal_total += 1
            payload = json.dumps({"key": f"n-{index}", "value": index})
        elif category == 1:
            markdown_total += 1
            payload = f"```json\n{json.dumps({'key': f'm-{index}', 'value': index})}\n```"
        elif category == 2:
            payload = f"Here is the JSON output: {json.dumps({'key': f'p-{index}', 'value': index})}"
        elif category == 3:
            truncated_total += 1
            if index % 20 < 17:
                payload = f'prefix {json.dumps({"key": f"t-{index}", "value": index})} trailing {{"incomplete":'
            else:
                payload = '{"key":"broken","value":'
        else:
            payload = f'{{"key":"extra-{index}","value":{index},}}'

        result = await parser.parse(payload, schema=_EntropyPayload)
        if category == 0 and result.success:
            normal_success += 1
        if category == 1 and result.success:
            markdown_success += 1
        if category == 3 and result.success:
            truncated_success += 1

    normal_rate = (normal_success / normal_total) * 100.0 if normal_total else 0.0
    markdown_rate = (markdown_success / markdown_total) * 100.0 if markdown_total else 0.0
    truncated_rate = (truncated_success / truncated_total) * 100.0 if truncated_total else 0.0
    false_positive = ((normal_total - normal_success) / normal_total) * 100.0 if normal_total else 0.0
    return {
        "normal_json_success_percent": normal_rate,
        "markdown_wrapped_success_percent": markdown_rate,
        "truncated_json_success_percent": truncated_rate,
        "false_positive_percent": false_positive,
    }


async def _exec_tc_er_002(case: HolographicCase) -> dict[str, float]:
    from polaris.kernelone.llm.robust_parser.correctors import ValidationErrorCorrector

    parser = RobustParser[_EntropyPayload](max_correction_turns=5, enable_correction=True, enable_fallback=True)
    total = max(100, case.min_samples)
    success_within_budget = 0
    attempt_samples: list[float] = []
    prompt_gen_ms: list[float] = []
    corrector = ValidationErrorCorrector()
    validation_error: ValidationError | None = None
    try:
        _EntropyPayload.model_validate({"key": "missing-value"})
    except ValidationError as exc:
        validation_error = exc

    for index in range(total):
        target_attempt = 1 + (index % 3)
        correction_calls = 0

        async def llm_corrector(
            _prompt: str,
            *,
            attempt_goal: int = target_attempt,
            payload_index: int = index,
        ) -> str:
            nonlocal correction_calls
            correction_calls += 1
            if correction_calls >= attempt_goal:
                return json.dumps({"key": f"fixed-{payload_index}", "value": payload_index})
            if correction_calls == attempt_goal - 1:
                return json.dumps({"key": f"partial-{payload_index}"})
            return "not-json"

        result = await parser.parse(
            json.dumps({"key": f"initial-{index}"}),
            schema=_EntropyPayload,
            llm_corrector=llm_corrector,
        )
        if result.success and result.correction_attempts <= 5:
            success_within_budget += 1
        attempt_samples.append(float(result.correction_attempts))

        if validation_error is not None:
            started = time.perf_counter_ns()
            _ = corrector.build_correction_prompt(validation_error, _EntropyPayload)
            prompt_gen_ms.append(_perf_ms(started))

    attempt_stats = summarize_samples(attempt_samples, warmup_rounds=case.warmup_rounds)
    prompt_stats = summarize_samples(prompt_gen_ms, warmup_rounds=case.warmup_rounds)
    return {
        "convergence_within_5_percent": (success_within_budget / total) * 100.0,
        "avg_attempts": attempt_stats.mean,
        "prompt_gen_p99_ms": prompt_stats.p99,
    }


async def _exec_tc_er_004(case: HolographicCase) -> dict[str, float]:
    total = max(1000, case.min_samples)
    old_runtime_ms: list[float] = []
    new_runtime_ms: list[float] = []
    consistent = 0

    for index in range(total):
        if index % 5 == 0:
            payload: Any = {"choices": [{"message": {"content": json.dumps({"key": index, "value": index})}}]}
        elif index % 5 == 1:
            payload = {"text": f"Answer {index}", "reasoning": f"why-{index}", "finish_reason": "stop"}
        elif index % 5 == 2:
            payload = {"message": {"content": [{"type": "text", "text": f"Chunk {index}"}]}}
        elif index % 5 == 3:
            payload = f"```json\n{json.dumps({'key': index, 'value': index})}\n```"
        else:
            payload = f"prefix {json.dumps({'key': index, 'value': index})} suffix"

        started = time.perf_counter_ns()
        old_text = LLMResponseParser.extract_text(payload)
        old_reasoning = LLMResponseParser.extract_reasoning(payload)
        old_finish = LLMResponseParser.extract_finish_reason(payload)
        old_json = LLMResponseParser.extract_json_object(old_text or str(payload))
        old_runtime_ms.append(_perf_ms(started))

        started = time.perf_counter_ns()
        new_text = ResponseNormalizer.extract_text(payload)
        new_reasoning = ResponseNormalizer.extract_reasoning(payload)
        new_finish = ResponseNormalizer.extract_finish_reason(payload)
        new_json = ResponseNormalizer.extract_json_object(new_text or str(payload))
        new_runtime_ms.append(_perf_ms(started))

        if (
            old_text == new_text
            and old_reasoning == new_reasoning
            and old_finish == new_finish
            and _serialized_json(old_json) == _serialized_json(new_json)
        ):
            consistent += 1

    old_total = sum(old_runtime_ms)
    new_total = sum(new_runtime_ms)
    overhead = ((new_total - old_total) / old_total) * 100.0 if old_total > 0 else 0.0
    return {
        "output_consistency_percent": (consistent / total) * 100.0,
        "runtime_overhead_percent": overhead,
    }
