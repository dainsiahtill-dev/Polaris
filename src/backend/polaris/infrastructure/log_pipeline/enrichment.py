"""LLM Enrichment Worker.

Background worker that asynchronously enhances log events with LLM analysis.
This runs as a background task that consumes normalized events and produces
enriched events with:
- Signal score (0-1)
- Summary
- Normalized fields
- Noise detection
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS
from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

from .canonical_event import (
    CanonicalLogEventV2,
    LogEnrichmentV1,
)

_logger = logging.getLogger(__name__)

# LLM enrichment prompts
ENRICHMENT_SYSTEM_PROMPT = """You are a log analysis assistant for Polaris.
Your task is to analyze runtime log events and provide:
1. A brief summary (max 100 chars)
2. Signal score (0-1): How important is this event for debugging?
3. Whether this is noise (boilerplate, heartbeat, repetitive)
4. Key normalized fields extracted from the event

Respond in JSON format:
{
  "summary": "brief summary",
  "signal_score": 0.0-1.0,
  "is_noise": true/false,
  "normalized_fields": {"key": "value"}
}"""


@dataclass
class EnrichmentConfig:
    """Configuration for LLM enrichment."""

    enabled: bool = True
    batch_size: int = 10
    interval_sec: float = 5.0
    timeout_sec: float = DEFAULT_SHORT_TIMEOUT_SECONDS
    min_signal_threshold: float = 0.3  # Below this is considered noise
    provider: str = "openai"
    model: str = "gpt-4o-mini"


class LLMEnrichmentWorker:
    """Background worker for LLM-based log enrichment.

    This worker:
    1. Polls the norm journal for new events
    2. Batches events and sends to LLM for analysis
    3. Writes enrichment results to the enriched journal
    4. Handles failures gracefully without blocking realtime
    """

    def __init__(
        self,
        workspace: str,
        run_id: str = "",
        config: EnrichmentConfig | None = None,
    ) -> None:
        """Initialize the enrichment worker.

        Args:
            workspace: Workspace directory
            run_id: Run identifier (empty for all runs)
            config: Enrichment configuration
        """
        self.workspace = os.path.abspath(workspace)
        self.run_id = run_id
        self.config = config or EnrichmentConfig()
        self._running = False
        self._task: asyncio.Task | None = None

        # Runtime paths
        if run_id:
            self.run_dir = os.path.join(self.workspace, "runtime", "runs", run_id, "logs")
        else:
            self.run_dir = os.path.join(self.workspace, "runtime", "logs")

        self.norm_path = os.path.join(self.run_dir, "journal.norm.jsonl")
        self.enriched_path = os.path.join(self.run_dir, "journal.enriched.jsonl")

        # Track processed events
        self._processed_ids: set = set()
        self._last_check_time = time.time()

    def _load_processed_ids(self) -> None:
        """Load already processed event IDs from enriched file."""
        if not os.path.exists(self.enriched_path):
            return

        try:
            with open(self.enriched_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event_id = data.get("event_id")
                        if event_id:
                            self._processed_ids.add(event_id)
                    except json.JSONDecodeError:
                        continue
        except (RuntimeError, ValueError) as exc:
            _logger.debug("enrichment file read failed: %s", exc)

    def _fetch_pending_events(self) -> list[CanonicalLogEventV2]:
        """Fetch events that need enrichment."""
        if not os.path.exists(self.norm_path):
            return []

        events = []
        with open(self.norm_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    event_id = data.get("event_id")

                    # Skip already processed
                    if event_id in self._processed_ids:
                        continue

                    # Skip if enrichment already exists and succeeded
                    enrichment = data.get("enrichment")
                    if enrichment and enrichment.get("status") == "success":
                        self._processed_ids.add(event_id)
                        continue

                    # Parse event
                    event = CanonicalLogEventV2(**data)
                    events.append(event)

                except (json.JSONDecodeError, ValueError):
                    continue

        return events

    def _batch_events(
        self,
        events: list[CanonicalLogEventV2],
        batch_size: int,
    ) -> list[list[CanonicalLogEventV2]]:
        """Batch events for processing."""
        batches = []
        for i in range(0, len(events), batch_size):
            batches.append(events[i : i + batch_size])
        return batches

    async def _enrich_with_llm(
        self,
        events: list[CanonicalLogEventV2],
    ) -> dict[str, LogEnrichmentV1]:
        """Send events to LLM for enrichment.

        Returns:
            Dict mapping event_id to enrichment result
        """
        if not events:
            return {}

        # Build prompt with events
        events_text = "\n".join([f"[{i + 1}] {e.channel}/{e.kind}: {e.message[:200]}" for i, e in enumerate(events)])

        prompt = f"""Analyze these log events and provide enrichment for each:

{events_text}

For each event, respond with JSON array:
[
  {{"index": 1, "summary": "...", "signal_score": 0.5, "is_noise": false, "normalized_fields": {{}}}},
  ...
]"""

        # Try to get LLM response
        try:
            result = await self._call_llm(prompt)
            return self._parse_enrichment_response(events, result)
        except (RuntimeError, ValueError):
            # Return failed enrichment for all events
            return {
                e.event_id: LogEnrichmentV1(
                    signal_score=0.0,
                    summary="",
                    normalized_fields={},
                    noise=True,
                    status="failed",
                    error=str(e),
                )
                for e in events
            }

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM for enrichment."""
        # This would integrate with the existing LLM provider system
        # For now, return a placeholder that marks events as processed

        # Try importing from existing LLM system via ServiceLocator
        provider = ServiceLocator.get_provider()
        if provider is None:
            # Fallback: return empty response (events will be retried)
            raise RuntimeError("LLM provider unavailable - ServiceLocator returned None")

        try:
            from polaris.kernelone.llm.toolkit.contracts import AIRequest, TaskType

            request = AIRequest(
                task_type=TaskType.GENERATION,
                role="system",
                input=f"{ENRICHMENT_SYSTEM_PROMPT}\n\n{prompt}",
                options={
                    "max_tokens": 2000,
                    "timeout": self.config.timeout_sec,
                },
            )

            response = await provider.generate(request)
            return response.output
        except (RuntimeError, ValueError) as e:
            # If LLM unavailable, return empty response (events will be retried)
            raise RuntimeError(f"LLM provider unavailable: {e}") from e

    def _parse_enrichment_response(
        self,
        events: list[CanonicalLogEventV2],
        response: str,
    ) -> dict[str, LogEnrichmentV1]:
        """Parse LLM response into enrichment results."""
        results = {}

        try:
            # Try to parse JSON array from response
            import re

            json_match = re.search(r"\[[\s\S]*\]", response)
            if json_match:
                enrichments = json.loads(json_match.group())
                for item in enrichments:
                    idx = item.get("index", 0) - 1
                    if 0 <= idx < len(events):
                        event = events[idx]
                        results[event.event_id] = LogEnrichmentV1(
                            summary=item.get("summary", "")[:100],
                            signal_score=float(item.get("signal_score", 0.5)),
                            noise=bool(item.get("is_noise", False)),
                            normalized_fields=item.get("normalized_fields", {}),
                            status="success",
                        )
        except (RuntimeError, ValueError) as exc:
            _logger.debug("enrichment file read failed: %s", exc)

        # Mark any missing events as failed
        for event in events:
            if event.event_id not in results:
                results[event.event_id] = LogEnrichmentV1(
                    signal_score=0.0,
                    summary="",
                    normalized_fields={},
                    noise=True,
                    status="failed",
                    error="Failed to parse LLM response",
                )

        return results

    def _write_enrichment(
        self,
        event: CanonicalLogEventV2,
        enrichment: LogEnrichmentV1,
    ) -> None:
        """Write enrichment result to enriched journal."""
        # Update event with enrichment
        enriched_event = event.model_copy()
        enriched_event.enrichment = enrichment

        # Append to enriched journal
        os.makedirs(os.path.dirname(self.enriched_path), exist_ok=True)
        line = json.dumps(enriched_event.model_dump(), ensure_ascii=False) + "\n"
        with open(self.enriched_path, "a", encoding="utf-8") as f:
            f.write(line)

        # Mark as processed
        self._processed_ids.add(event.event_id)

    async def _process_loop(self) -> None:
        """Main processing loop."""
        self._load_processed_ids()

        while self._running:
            try:
                # Fetch pending events
                events = self._fetch_pending_events()

                if events:
                    # Batch and process
                    batches = self._batch_events(events, self.config.batch_size)
                    for batch in batches:
                        enrichments = await self._enrich_with_llm(batch)
                        for event in batch:
                            enrichment = enrichments.get(
                                event.event_id,
                                LogEnrichmentV1(
                                    status="failed",
                                    error="No enrichment returned",
                                ),
                            )
                            self._write_enrichment(event, enrichment)

                # Wait before next poll
                await asyncio.sleep(self.config.interval_sec)

            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError):
                # Log but continue
                await asyncio.sleep(1.0)

    async def start(self) -> None:
        """Start the enrichment worker."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        """Stop the enrichment worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


class EnrichmentWorkerPool:
    """Pool of enrichment workers for multiple runs."""

    def __init__(self, workspace: str) -> None:
        self.workspace = os.path.abspath(workspace)
        self._workers: dict[str, LLMEnrichmentWorker] = {}
        self._config = EnrichmentConfig()

    def get_worker(self, run_id: str = "") -> LLMEnrichmentWorker:
        """Get or create worker for a run."""
        if run_id not in self._workers:
            self._workers[run_id] = LLMEnrichmentWorker(
                workspace=self.workspace,
                run_id=run_id,
                config=self._config,
            )
        return self._workers[run_id]

    async def start_all(self) -> None:
        """Start all workers."""
        for worker in self._workers.values():
            await worker.start()

    async def stop_all(self) -> None:
        """Stop all workers."""
        for worker in self._workers.values():
            await worker.stop()
        self._workers.clear()


# Global pool instance
_enrichment_pool: EnrichmentWorkerPool | None = None


def get_enrichment_pool(workspace: str) -> EnrichmentWorkerPool:
    """Get the global enrichment worker pool."""
    global _enrichment_pool
    if _enrichment_pool is None:
        _enrichment_pool = EnrichmentWorkerPool(workspace)
    return _enrichment_pool
