"""Delivery adapter for pm_planning pipeline ports.

This module is the thin bridge between the Cell layer (pm_planning) and the
delivery layer (polaris.delivery.cli.pm.backend).  It wraps the concrete
delivery implementations behind the PmInvokeBackendPort protocol.

This module MAY import ``polaris.delivery.*`` freely since it lives in the
delivery layer.

Design invariant: the delivery layer MUST NOT be imported by any Cell module.
Only this adapter (loaded lazily by pipeline_ports.py) imports delivery.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DeliveryPmInvokePort"]


class DeliveryPmInvokePort:
    """Concrete implementation of PmInvokeBackendPort backed by polaris.delivery.

    This class is loaded lazily by ``pipeline_ports.get_pm_invoke_port()`` so
    that ``polaris.cells.orchestration.pm_planning.pipeline`` never imports
    delivery at load time.
    """

    def invoke(
        self,
        state: Any,
        prompt: str,
        backend_kind: str,
        args: Any,
        usage_ctx: Any,
    ) -> str:
        """Invoke the PM LLM backend via delivery layer."""
        from polaris.delivery.cli.pm.backend import invoke_pm_backend

        return invoke_pm_backend(
            state=state,
            prompt=prompt,
            backend_kind=backend_kind,
            args=args,
            usage_ctx=usage_ctx,
        )

    def build_prompt(
        self,
        requirements: str,
        plan_text: str,
        gap_report: str,
        last_qa: str,
        last_tasks: Any,
        director_result: Any,
        pm_state: Any,
        iteration: int = 0,
        run_id: str = "",
        events_path: str = "",
        workspace_root: str = "",
    ) -> str:
        """Build PM planning prompt via delivery layer."""
        from polaris.delivery.cli.pm.backend import build_pm_prompt

        return build_pm_prompt(
            requirements=requirements,
            plan_text=plan_text,
            gap_report=gap_report,
            last_qa=last_qa,
            last_tasks=last_tasks,
            director_result=director_result,
            pm_state=pm_state,
            iteration=iteration,
            run_id=run_id,
            events_path=events_path,
            workspace_root=workspace_root,
        )

    def extract_json(self, raw_output: str) -> dict[str, Any] | None:
        """Extract JSON from LLM output via delivery layer."""
        from polaris.delivery.cli.pm.backend import _extract_json_from_llm_output

        return _extract_json_from_llm_output(raw_output)
