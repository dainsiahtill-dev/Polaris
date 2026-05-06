from __future__ import annotations

from polaris.kernelone.prompts import loader


def test_kernelone_prompt_loader_resolves_repo_prompt_templates() -> None:
    loader.load_profile.cache_clear()

    data = loader.load_profile("zhenguan_governance")

    assert data.get("id") == "zhenguan_governance"
    assert "agents_prompt" in data.get("templates", {})


def test_kernelone_prompt_loader_falls_back_to_default_profile() -> None:
    loader.load_profile.cache_clear()

    data = loader.load_profile("missing-profile")

    assert data.get("id") == "zhenguan_governance"
