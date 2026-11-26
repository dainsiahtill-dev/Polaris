"""回归：发布器空闲等待期间事件循环必须继续转动。

旧版在 `queue.get()` 上同步阻塞，循环停转 → NATS 客户端的 ping 任务不执行 →
空闲超过 ping_interval × max_outstanding_pings 被服务端断开（实测每个 bench
项目启动时一次 `Publish timeout` + `NATS disconnected`）。
"""

from __future__ import annotations

import asyncio
import threading
import time

from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher


def test_event_loop_keeps_turning_while_waiting_for_work(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "0")
    publisher = JetStreamPublisher()
    loop = asyncio.new_event_loop()
    publisher._loop = loop
    worker = threading.Thread(
        target=loop.run_until_complete, args=(publisher._run(),), daemon=True
    )
    worker.start()
    try:
        turned: list[float] = []

        async def beat() -> None:
            while True:
                turned.append(time.monotonic())
                await asyncio.sleep(0.02)

        asyncio.run_coroutine_threadsafe(beat(), loop)
        time.sleep(0.5)
        assert len(turned) >= 10, f"空闲期循环停转：0.5s 内只调度了 {len(turned)} 次"
    finally:
        publisher.stop()
        worker.join(timeout=5)
