"""Tests for skill_loader module."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.runtime.internal.skill_loader import (
    RoleSkillManager,
    Skill,
    SkillLoader,
    create_role_skill_manager,
    create_skill_loader,
)


class TestSkill:
    """Tests for Skill dataclass."""

    def test_creation(self):
        skill = Skill(
            name="task-planning",
            description="Plan tasks effectively",
            body="## Planning\n\n1. Analyze...",
            tags=["planning", "pm"],
            path="/skills/task-planning.md",
            metadata={"version": "1.0"},
        )
        assert skill.name == "task-planning"
        assert skill.description == "Plan tasks effectively"
        assert "planning" in skill.tags
        assert skill.path == "/skills/task-planning.md"


class TestSkillLoader:
    """Tests for SkillLoader两层技能加载系统."""

    def test_default_initialization(self):
        loader = SkillLoader()
        assert loader.skills_dir is None
        assert loader.skills == {}

    def test_empty_skills_dir(self):
        loader = SkillLoader(skills_dir=Path("/nonexistent"))
        assert loader.skills == {}

    def test_resolve_default_skills_dir(self):
        path = SkillLoader.resolve_default_skills_dir("/workspace")
        # On Windows, Path("/workspace").resolve() becomes C:\workspace
        # So we just check the final path ends with .skills
        assert str(path).endswith(".skills")

    def test_parse_frontmatter_simple(self):
        meta, body = SkillLoader._parse_frontmatter(
            "---\nname: test-skill\ndescription: A test skill\n---\n## Body\n\nContent here."
        )
        assert meta["name"] == "test-skill"
        assert meta["description"] == "A test skill"
        assert "## Body" in body

    def test_parse_frontmatter_empty(self):
        meta, body = SkillLoader._parse_frontmatter("No frontmatter, just body.")
        assert meta == {}
        assert body == "No frontmatter, just body."

    def test_parse_frontmatter_multiline(self):
        text = "---\nname: multi\ntags: a, b, c\n---\nSkill content."
        meta, _ = SkillLoader._parse_frontmatter(text)
        assert meta["name"] == "multi"
        assert meta["tags"] == "a, b, c"

    def test_parse_tags_comma_separated_string(self):
        tags = SkillLoader._parse_tags("planning, pm, task")
        assert "planning" in tags
        assert "pm" in tags
        assert "task" in tags

    def test_parse_tags_bracket_format(self):
        tags = SkillLoader._parse_tags("[planning, pm, task]")
        assert "planning" in tags
        assert "pm" in tags

    def test_parse_tags_empty(self):
        tags = SkillLoader._parse_tags("")
        assert tags == []

    def test_parse_tags_with_whitespace(self):
        tags = SkillLoader._parse_tags("  planning  ,  pm  ")
        assert "planning" in tags
        assert "pm" in tags

    def test_parse_tags_single_value(self):
        tags = SkillLoader._parse_tags("planning")
        assert tags == ["planning"]

    def test_get_manifest_empty(self):
        loader = SkillLoader()
        manifest = loader.get_manifest()
        assert manifest == []

    def test_get_content_unknown_skill(self):
        loader = SkillLoader()
        result = loader.get_content("nonexistent")
        assert "Unknown skill" in result
        assert "nonexistent" in result

    def test_has_skill(self):
        loader = SkillLoader()
        assert loader.has_skill("any") is False

    def test_list_skills_empty(self):
        loader = SkillLoader()
        assert loader.list_skills() == []

    def test_list_skills_with_tag_filter(self):
        loader = SkillLoader()
        result = loader.list_skills(tag="planning")
        assert result == []


class TestSkillLoaderWithFiles:
    """Tests for SkillLoader with actual skill files."""

    def test_load_skill_file_markdown(self, tmp_path):
        skill_file = tmp_path / "read-file.md"
        skill_file.write_text(
            "---\nname: read-file\ndescription: Read file contents\ntags: [file, read]\n---\n## Read File\n\nUse glob to find files then read.",
            encoding="utf-8",
        )

        loader = SkillLoader(skills_dir=tmp_path)
        assert loader.has_skill("read-file")
        assert len(loader.skills) == 1

        skill = loader.skills["read-file"]
        assert skill.name == "read-file"
        assert skill.description == "Read file contents"
        assert "file" in skill.tags

    def test_load_skill_file_no_frontmatter(self, tmp_path):
        skill_file = tmp_path / "no-frontmatter.md"
        skill_file.write_text("Just plain content without frontmatter.", encoding="utf-8")

        loader = SkillLoader(skills_dir=tmp_path)
        assert loader.has_skill("no-frontmatter")

        skill = loader.skills["no-frontmatter"]
        assert skill.name == "no-frontmatter"
        assert skill.description == ""

    def test_load_multiple_skills_sorted(self, tmp_path):
        (tmp_path / "z-skill.md").write_text("---\nname: z-skill\ndescription: Z\n---\nZ content.", encoding="utf-8")
        (tmp_path / "a-skill.md").write_text("---\nname: a-skill\ndescription: A\n---\nA content.", encoding="utf-8")

        loader = SkillLoader(skills_dir=tmp_path)
        names = [s.name for s in loader.skills.values()]
        assert names == ["a-skill", "z-skill"]

    def test_get_manifest_text_empty(self):
        loader = SkillLoader()
        text = loader.get_manifest_text()
        assert text == "(no skills available)"

    def test_get_manifest_text_with_skills(self, tmp_path):
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text(
            "---\nname: test-skill\ndescription: A test skill\ntags: [test]\n---\nContent.",
            encoding="utf-8",
        )

        loader = SkillLoader(skills_dir=tmp_path)
        text = loader.get_manifest_text()
        assert "test-skill" in text
        assert "A test skill" in text
        assert "[test]" in text

    def test_get_content_returns_formatted_skill(self, tmp_path):
        skill_file = tmp_path / "planning.md"
        skill_file.write_text(
            "---\nname: planning\ndescription: Task planning\n---\n## Planning Phase\n\n1. Analyze requirements",
            encoding="utf-8",
        )

        loader = SkillLoader(skills_dir=tmp_path)
        content = loader.get_content("planning")
        assert "<skill name=" in content
        assert "planning" in content
        assert "## Planning Phase" in content
        assert "</skill>" in content


class TestRoleSkillManager:
    """Tests for RoleSkillManager."""

    def test_default_initialization(self):
        manager = RoleSkillManager()
        assert manager.loader is not None
        assert manager._role_skills == {}

    def test_register_role_skills(self):
        manager = RoleSkillManager()
        manager.register_role_skills("PM", ["task-planning", "priority-management"])
        assert manager._role_skills["PM"] == ["task-planning", "priority-management"]

    def test_get_role_manifest_unknown_role(self):
        manager = RoleSkillManager()
        manifest = manager.get_role_manifest("UnknownRole")
        # Falls back to DEFAULT_SKILLS for unknown role
        assert "(no skills)" in manifest or "task-planning" in manifest

    def test_load_role_skill_noop(self):
        manager = RoleSkillManager()
        result = manager.load_role_skill("PM", "nonexistent")
        # No skills loaded, returns error message
        assert "Unknown skill" in result


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_skill_loader(self, tmp_path):
        loader = create_skill_loader(tmp_path)
        assert isinstance(loader, SkillLoader)
        assert loader.skills_dir == tmp_path

    def test_create_role_skill_manager(self, tmp_path):
        manager = create_role_skill_manager(tmp_path)
        assert isinstance(manager, RoleSkillManager)
        assert manager.loader is not None
