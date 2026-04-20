"""Evaluation Framework - Test Suites"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from polaris.cells.llm.provider_runtime.public.service import get_provider_manager

from .constants import INTERVIEW_SEMANTIC_ENABLED
from .utils import (
    looks_like_deflection,
    semantic_criteria_hits,
    split_thinking_output,
)


async def run_connectivity_suite(
    provider_cfg: dict[str, Any],
    model: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    """运行连接性测试套件"""
    provider_type = str(provider_cfg.get("type") or "").strip().lower()

    if not provider_type:
        return {
            "ok": False,
            "error": "Provider type not specified",
            "details": {},
        }

    provider = get_provider_manager().get_provider_instance(provider_type)
    if provider is None:
        return {
            "ok": False,
            "error": f"Provider not found: {provider_type}",
            "details": {},
        }

    # 应用 API key
    if api_key:
        provider_cfg = {**provider_cfg, "api_key": api_key}

    # 健康检查
    try:
        result = await asyncio.to_thread(provider.health, provider_cfg)
        if not result.ok:
            return {
                "ok": False,
                "error": result.error or "Health check failed",
                "details": {"health": {"status": "unhealthy", "error": result.error}},
            }
        details: dict[str, Any] = {
            "health": {"status": "healthy", "latency_ms": result.latency_ms},
        }
        total_latency_ms = int(result.latency_ms or 0)
    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
            "details": {"health": {"status": "error", "error": str(e)}},
        }

    # Ollama 连通性测试需要“真实模型可用性”验证：
    # 1) 模型存在（list_models）
    # 2) 轻量推理请求（invoke）
    if provider_type == "ollama":
        requested_model = str(model or "").strip()

        try:
            models_result = await asyncio.to_thread(provider.list_models, provider_cfg)
        except (RuntimeError, ValueError) as e:
            return {
                "ok": False,
                "error": f"Failed to list models: {e}",
                "details": {
                    **details,
                    "model_available": {"status": "error", "error": str(e)},
                },
            }

        if not bool(getattr(models_result, "ok", False)):
            model_error = str(getattr(models_result, "error", "") or "list_models failed")
            return {
                "ok": False,
                "error": f"Model list check failed: {model_error}",
                "details": {
                    **details,
                    "model_available": {"status": "error", "error": model_error},
                },
            }

        available_models: list[str] = []
        for item in list(getattr(models_result, "models", []) or []):
            model_id = str(getattr(item, "id", "") or "").strip()
            if model_id:
                available_models.append(model_id)
        available_lookup = {name.lower() for name in available_models}
        model_available = requested_model.lower() in available_lookup
        if not model_available and ":" not in requested_model:
            model_available = any(name.split(":", 1)[0] == requested_model.lower() for name in available_lookup)

        if not model_available:
            message = f"Ollama model not installed: {requested_model}"
            return {
                "ok": False,
                "error": message,
                "details": {
                    **details,
                    "model_available": {
                        "status": "unavailable",
                        "error": message,
                        "requested": requested_model,
                        "count": len(available_models),
                    },
                },
            }

        details["model_available"] = {
            "status": "available",
            "requested": requested_model,
            "count": len(available_models),
        }

        smoke_cfg = dict(provider_cfg)
        options = smoke_cfg.get("options")
        options_payload = dict(options) if isinstance(options, dict) else {}
        options_payload.setdefault("num_predict", 8)
        options_payload.setdefault("temperature", 0.0)
        options_payload.setdefault("top_p", 1.0)
        options_payload.setdefault("top_k", 1)
        smoke_cfg["options"] = options_payload

        try:
            smoke_result = await asyncio.to_thread(
                provider.invoke,
                "Reply with exactly: OK",
                requested_model,
                smoke_cfg,
            )
        except (RuntimeError, ValueError) as e:
            return {
                "ok": False,
                "error": f"Ollama invoke check failed: {e}",
                "details": {
                    **details,
                    "invoke_smoke": {"status": "error", "error": str(e)},
                },
            }

        if not bool(getattr(smoke_result, "ok", False)):
            invoke_error = str(getattr(smoke_result, "error", "") or "invoke failed")
            return {
                "ok": False,
                "error": f"Ollama invoke check failed: {invoke_error}",
                "details": {
                    **details,
                    "invoke_smoke": {"status": "failed", "error": invoke_error},
                },
            }

        invoke_latency = int(getattr(smoke_result, "latency_ms", 0) or 0)
        total_latency_ms += invoke_latency
        details["invoke_smoke"] = {"status": "ok", "latency_ms": invoke_latency}

    return {
        "ok": True,
        "details": details,
        "latency_ms": total_latency_ms,
    }


def run_connectivity_suite_sync(
    provider_cfg: dict[str, Any],
    model: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    """同步运行连接性测试套件

    Safe to call from both synchronous and asynchronous contexts.  When an
    event loop is already running in the current thread (e.g. inside a FastAPI
    route or an async test), a fresh event loop is created in a dedicated
    worker thread so that ``asyncio.run`` never sees a pre-existing loop.
    """
    try:
        # Probe for a running event loop in the current thread.
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # There is already a running loop — run the coroutine in an isolated
        # thread that owns its own event loop to avoid the
        # "This event loop is already running" RuntimeError.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                run_connectivity_suite(provider_cfg, model, api_key),
            )
            try:
                return future.result()
            except (RuntimeError, ValueError) as e:
                return {
                    "ok": False,
                    "error": str(e),
                    "details": {},
                }
    else:
        try:
            return asyncio.run(run_connectivity_suite(provider_cfg, model, api_key))
        except (RuntimeError, ValueError) as e:
            return {
                "ok": False,
                "error": str(e),
                "details": {},
            }


async def run_response_suite(
    provider_cfg: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """运行响应测试套件"""
    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    provider = get_provider_manager().get_provider_instance(provider_type)

    if provider is None:
        return {
            "ok": False,
            "error": f"Provider not found: {provider_type}",
        }

    test_prompt = 'Respond with exactly: {"status": "ok", "test": true}'

    try:
        result = await asyncio.to_thread(
            provider.invoke,
            test_prompt,
            model,
            {**provider_cfg, "temperature": 0.0, "max_tokens": 100},
        )

        if not result.ok:
            return {
                "ok": False,
                "error": result.error or "Invoke failed",
            }

        # 尝试解析 JSON
        output = str(result.output or "")
        has_json = '"status"' in output and '"ok"' in output

        return {
            "ok": has_json,
            "details": {
                "output_length": len(output),
                "has_json": has_json,
            },
            "latency_ms": result.latency_ms,
        }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


async def run_thinking_suite(
    provider_cfg: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """运行思考能力测试套件"""
    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    provider = get_provider_manager().get_provider_instance(provider_type)

    if provider is None:
        return {
            "ok": False,
            "error": f"Provider not found: {provider_type}",
        }

    test_prompt = (
        "Analyze this step by step:\n"
        "If a train travels 60 km in 30 minutes, what is its average speed in km/h?\n"
        "Wrap your thinking in <thinking> tags and final answer in <answer> tags."
    )

    try:
        result = await asyncio.to_thread(
            provider.invoke,
            test_prompt,
            model,
            {**provider_cfg, "temperature": 0.2},
        )

        if not result.ok:
            return {
                "ok": False,
                "error": result.error or "Invoke failed",
            }

        output = str(result.output or "")
        thinking, answer = split_thinking_output(output)

        has_thinking = len(thinking) > 10
        has_answer = len(answer) > 5
        looks_reasonable = "120" in output or "120 km/h" in output.lower()

        return {
            "ok": has_thinking and has_answer and looks_reasonable,
            "details": {
                "thinking": {
                    "has_thinking": has_thinking,
                    "length": len(thinking),
                },
                "answer": {
                    "has_answer": has_answer,
                    "length": len(answer),
                    "looks_reasonable": looks_reasonable,
                },
            },
            "latency_ms": result.latency_ms,
        }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


async def run_qualification_suite(
    provider_cfg: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """运行资质测试套件"""
    cases = [
        {
            "id": "json_basic",
            "name": "Basic JSON Response",
            "prompt": 'Return only: {"result": "success", "value": 42}',
            "validator": lambda x: '"result"' in x and '"success"' in x,
        },
        {
            "id": "list_format",
            "name": "List Formatting",
            "prompt": "List 3 colors as a JSON array",
            "validator": lambda x: "[" in x and "]" in x,
        },
        {
            "id": "no_deflection",
            "name": "No Deflection",
            "prompt": "Write a simple Python hello world function",
            "validator": lambda x: not looks_like_deflection(x) and "def " in x.lower(),
        },
    ]

    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    provider = get_provider_manager().get_provider_instance(provider_type)

    if provider is None:
        return {
            "ok": False,
            "error": f"Provider not found: {provider_type}",
            "cases": [],
        }

    results = []
    passed = 0

    for case in cases:
        try:
            # provider.invoke is dynamically typed, suppress mypy error
            invoke_fn = provider.invoke  # type: ignore[assignment]
            prompt = str(case["prompt"])
            result = await asyncio.to_thread(
                invoke_fn,
                prompt,
                str(model),
                {**provider_cfg, "temperature": 0.2},
            )

            output = str(result.output or "")
            validator = case.get("validator")
            case_passed = result.ok and callable(validator) and validator(output)

            if case_passed:
                passed += 1

            results.append(
                {
                    "id": case["id"],
                    "name": case["name"],
                    "passed": case_passed,
                    "output": output[:200],
                }
            )
        except (RuntimeError, ValueError) as e:
            results.append(
                {
                    "id": case["id"],
                    "name": case["name"],
                    "passed": False,
                    "error": str(e),
                }
            )

    total = len(cases)
    return {
        "ok": passed >= total * 0.6,  # 60% pass rate
        "details": {"cases": results, "passed": passed, "total": total},
        "score": passed / total if total > 0 else 0.0,
    }


async def run_interview_suite(
    provider_cfg: dict[str, Any],
    model: str,
    role: str = "general",
) -> dict[str, Any]:
    """运行面试测试套件"""
    questions = [
        {
            "id": "q1",
            "question": "How do you handle tight deadlines?",
            "criteria": ["prioritize", "communicate", "scope", "quality"],
        },
        {
            "id": "q2",
            "question": "Describe your approach to debugging a complex issue.",
            "criteria": ["systematic", "reproduce", "isolate", "test"],
        },
    ]

    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    provider = get_provider_manager().get_provider_instance(provider_type)

    if provider is None:
        return {
            "ok": False,
            "error": f"Provider not found: {provider_type}",
        }

    total_score = 0.0
    results = []

    for q in questions:
        prompt = f"""You are a job candidate. Answer this interview question directly and professionally.

Question: {q["question"]}

Provide your answer in <thinking> and <answer> tags."""

        try:
            result = await asyncio.to_thread(
                provider.invoke,
                prompt,
                model,
                {**provider_cfg, "temperature": 0.3},
            )

            output = str(result.output or "")
            _thinking, answer = split_thinking_output(output)

            # 语义评分
            if INTERVIEW_SEMANTIC_ENABLED and len(answer) >= 80:
                hits = semantic_criteria_hits(answer, list(q["criteria"]))  # type: ignore[arg-type]
                score = sum(hits.values()) / len(hits) if hits else 0.5
            else:
                score = 0.5 if len(answer) > 50 else 0.0

            total_score += score

            results.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "score": score,
                    "passed": score > 0.5,
                }
            )

        except (RuntimeError, ValueError) as e:
            results.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "score": 0.0,
                    "passed": False,
                    "error": str(e),
                }
            )

    avg_score = total_score / len(questions) if questions else 0.0

    return {
        "ok": avg_score > 0.6,
        "details": {"results": results},
        "score": avg_score,
    }


__all__ = [
    "run_connectivity_suite",
    "run_connectivity_suite_sync",
    "run_interview_suite",
    "run_qualification_suite",
    "run_response_suite",
    "run_thinking_suite",
]
