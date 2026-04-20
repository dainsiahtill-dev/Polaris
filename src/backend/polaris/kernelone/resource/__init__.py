"""Resource quota management for KernelOne agents and cells.

This package provides the resource quota system for Polaris AGI skeleton,
enabling per-agent resource tracking and enforcement.

Exports:
    ResourceQuota: Immutable resource limits for an agent
    ResourceUsage: Current resource consumption tracker
    AgentResources: Combines quota and usage for an agent
    QuotaStatus: Enum for quota check results
    ResourceQuotaManager: Central manager for quota allocation and checking
    get_global_quota_manager: Get the global manager singleton
    reset_global_quota_manager: Reset the global manager (testing only)
    agent_quota_context: Context manager for agent-scoped quota
    QuotaMetrics: Prometheus-compatible quota metrics
    get_quota_metrics: Get the global metrics singleton
"""

from __future__ import annotations

from polaris.kernelone.resource.quota import (
    AgentResources,
    QuotaMetrics,
    QuotaStatus,
    ResourceQuota,
    ResourceQuotaManager,
    ResourceUsage,
    agent_quota_context,
    get_global_quota_manager,
    get_quota_metrics,
    reset_global_quota_manager,
)

__all__ = [
    "AgentResources",
    "QuotaMetrics",
    "QuotaStatus",
    "ResourceQuota",
    "ResourceQuotaManager",
    "ResourceUsage",
    "agent_quota_context",
    "get_global_quota_manager",
    "get_quota_metrics",
    "reset_global_quota_manager",
]
