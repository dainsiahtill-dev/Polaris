"""Preference Learner - 用户偏好学习."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.role.routing.context import RoutingContext

logger = logging.getLogger(__name__)


@dataclass
class Feedback:
    """用户反馈"""

    session_id: str
    persona_id: str
    score: float  # 1.0 = 完全满意, 0.0 = 不满意
    timestamp: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersonaPreference:
    """Persona 偏好记录"""

    persona_id: str
    total_score: float = 0.0
    count: int = 0
    last_used: float = 0.0

    @property
    def average_score(self) -> float:
        return self.total_score / self.count if self.count > 0 else 0.5


class PreferenceLearner:
    """用户偏好学习器

    根据用户反馈学习 persona 偏好,用于提供更个性化的路由决策。
    """

    def __init__(self, workspace: str = "") -> None:
        self._workspace = workspace
        self._feedback_history: list[Feedback] = []
        self._persona_scores: dict[str, dict[str, PersonaPreference]] = {}  # user_id -> persona_id -> pref

    def record_feedback(
        self,
        session_id: str,
        persona_id: str,
        feedback: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """记录用户反馈

        Args:
            session_id: 会话 ID
            persona_id: 使用的 Persona ID
            feedback: 反馈分数 (1.0 = 完全满意, 0.0 = 不满意)
            context: 上下文信息 (可选)
        """
        fb = Feedback(
            session_id=session_id,
            persona_id=persona_id,
            score=feedback,
            context=context or {},
        )

        self._feedback_history.append(fb)

        # 更新 persona 评分
        if session_id not in self._persona_scores:
            self._persona_scores[session_id] = {}

        if persona_id not in self._persona_scores[session_id]:
            self._persona_scores[session_id][persona_id] = PersonaPreference(persona_id=persona_id)

        pref = self._persona_scores[session_id][persona_id]
        pref.total_score += feedback
        pref.count += 1
        pref.last_used = time.time()

        logger.info(f"Recorded feedback: session={session_id}, persona={persona_id}, score={feedback}")

    def get_preferred_personas(
        self,
        session_id: str,
        context: RoutingContext | None = None,
    ) -> list[str]:
        """获取用户偏好的 persona 列表

        Args:
            session_id: 会话 ID
            context: 上下文信息 (用于风格匹配)

        Returns:
            按偏好程度排序的 persona ID 列表
        """
        if session_id not in self._persona_scores:
            return ["gongbu_shilang"]  # 默认

        prefs = self._persona_scores[session_id]

        # 按平均分排序
        sorted_prefs = sorted(
            prefs.values(),
            key=lambda p: (p.average_score, p.count, p.last_used),
            reverse=True,
        )

        result = [p.persona_id for p in sorted_prefs]

        # 如果 context 指定了风格偏好,进一步过滤
        if context and context.user_preference:
            pref = context.user_preference
            if pref.formality == "casual":
                # 优先返回 casual 风格的 persona
                casual_personas = ["cyberpunk_hacker", "casual", "relaxed"]
                for cp in casual_personas:
                    if cp in result:
                        result.remove(cp)
                        result.insert(0, cp)

        return result

    def get_persona_score(self, session_id: str, persona_id: str) -> float | None:
        """获取特定 persona 的评分"""
        if session_id not in self._persona_scores:
            return None
        pref = self._persona_scores[session_id].get(persona_id)
        return pref.average_score if pref else None

    def save(self, path: Path | None = None) -> None:
        """持久化偏好数据"""
        if path is None:
            metadata_dir = get_workspace_metadata_dir_name()
            path = Path(self._workspace) / metadata_dir / "preference_learning.json"

        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "feedback_history": [
                {
                    "session_id": fb.session_id,
                    "persona_id": fb.persona_id,
                    "score": fb.score,
                    "timestamp": fb.timestamp,
                    "context": fb.context,
                }
                for fb in self._feedback_history[-100:]  # 只保留最近 100 条
            ],
            "persona_scores": {
                user_id: {
                    pid: {"total": p.total_score, "count": p.count, "last": p.last_used} for pid, p in prefs.items()
                }
                for user_id, prefs in self._persona_scores.items()
            },
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved preference data to {path}")

    def load(self, path: Path | None = None) -> None:
        """加载偏好数据"""
        if path is None:
            metadata_dir = get_workspace_metadata_dir_name()
            path = Path(self._workspace) / metadata_dir / "preference_learning.json"

        if not path.exists():
            logger.debug(f"Preference file not found: {path}")
            return

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            # 恢复反馈历史
            self._feedback_history = [
                Feedback(
                    session_id=fb["session_id"],
                    persona_id=fb["persona_id"],
                    score=fb["score"],
                    timestamp=fb["timestamp"],
                    context=fb.get("context", {}),
                )
                for fb in data.get("feedback_history", [])
            ]

            # 恢复 persona 评分
            self._persona_scores = {}
            for user_id, prefs in data.get("persona_scores", {}).items():
                self._persona_scores[user_id] = {}
                for pid, scores in prefs.items():
                    self._persona_scores[user_id][pid] = PersonaPreference(
                        persona_id=pid,
                        total_score=scores["total"],
                        count=scores["count"],
                        last_used=scores["last"],
                    )

            logger.info(f"Loaded preference data from {path}")

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to load preference data: {e}")
