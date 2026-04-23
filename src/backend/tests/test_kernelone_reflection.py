from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from polaris.kernelone.memory.reflection import ReflectionGenerator, ReflectionStore
from polaris.kernelone.memory.schema import MemoryItem, ReflectionNode


def test_reflection_store_loads_existing_records(tmp_path: Path) -> None:
    reflection_file = tmp_path / "runtime" / "REFLECTIONS.jsonl"
    reflection_file.parent.mkdir(parents=True, exist_ok=True)
    reflection_file.write_text(
        ReflectionNode(
            created_step=10,
            expiry_steps=50,
            type="heuristic",
            scope=["general"],
            confidence=0.8,
            text="remember this",
            evidence_mem_ids=["mem_1"],
            importance=5,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    store = ReflectionStore(str(reflection_file))

    assert len(store.reflections) == 1
    assert store.retrieve_active(current_step=20)[0].text == "remember this"


async def test_reflection_generator_accepts_structured_ollama_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "polaris.kernelone.memory.reflection.get_template",
        lambda _name: "{{memories_text}}",
    )
    monkeypatch.setattr(
        "polaris.kernelone.memory.reflection.invoke_ollama",
        lambda *args, **kwargs: SimpleNamespace(
            output='[{"scope":["general"],"text":"derived insight","confidence":0.9,"expiry_steps":8}]'
        ),
    )

    generator = ReflectionGenerator(model="demo", workspace_root=str(tmp_path))
    memories = [
        MemoryItem(
            source_event_id="evt_1",
            step=1,
            timestamp=datetime.now(),
            role="pm",
            type="observation",
            kind="info",
            text="important context",
            importance=5,
            keywords=["important"],
            hash="hash_1",
            context={"run_id": "run_1"},
        )
    ]

    result = await generator.generate(memories, current_step=12)

    assert len(result) == 1
    assert result[0].text == "derived insight"
    assert result[0].created_step == 12
