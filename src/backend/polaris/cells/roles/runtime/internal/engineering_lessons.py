"""Workspace-scoped cross-run lessons.

Weak models repeat the same failure patterns across runs (write-many-turns-
before-looking recurred verbatim in a later run). When QA rejects a candidate,
the distilled major finding is persisted here; the next run's Director
instruction carries the most recent lessons so the pattern is not rediscovered
from scratch. Bounded: three lessons, two hundred characters each.
"""

from __future__ import annotations

import json
from pathlib import Path

_LESSON_LIMIT = 3
_LESSON_MAX_CHARS = 200
_FILE_NAME = "engineering-lessons.jsonl"


def _path(workspace: Path) -> Path:
    return workspace / ".polaris" / "runtime" / _FILE_NAME


def record_lesson(workspace: Path, lesson: str) -> None:
    text = " ".join((lesson or "").split())[:_LESSON_MAX_CHARS]
    if not text:
        return
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"lesson": text}, ensure_ascii=False) + "\n")


def load_lessons(workspace: Path) -> list[str]:
    path = _path(workspace)
    if not path.is_file():
        return []
    lessons: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-_LESSON_LIMIT:]:
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            lesson = str(payload.get("lesson") or "").strip()
            if lesson:
                lessons.append(lesson)
    except OSError:
        return []
    return lessons


def lessons_guidance(workspace: Path) -> str:
    lessons = load_lessons(workspace)
    if not lessons:
        return ""
    numbered = "\n".join(f"{index + 1}. {lesson}" for index, lesson in enumerate(lessons))
    return (
        "Lessons from earlier rejected attempts in this workspace (do not repeat them):\n"
        + numbered
        + "\n\n"
    )
