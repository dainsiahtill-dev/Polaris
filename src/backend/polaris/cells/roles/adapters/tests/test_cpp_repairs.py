from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.cpp_repairs import (
    repair_cpp_failing_smoke_translation_units,
    repair_cpp_include_paths,
    repair_cpp_invalid_placeholder_declarations,
    repair_cpp_missing_private_members,
    repair_cpp_missing_standard_includes,
    repair_cpp_struct_getter_field_access,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_repair_cpp_include_paths_prefers_source_relative_paths(tmp_path: Path) -> None:
    _write(tmp_path / "src/engine/generator.hpp", "#pragma once\n")
    _write(tmp_path / "src/models/postcard.hpp", "#pragma once\n")
    _write(tmp_path / "src/utils/poem_library.hpp", "#pragma once\n")
    _write(
        tmp_path / "src/engine/generator.cpp",
        "\n".join(
            [
                '#include "src/engine/generator.hpp"',
                '#include "src/models/postcard.hpp"',
                '#include "src/utils/poem_library.hpp"',
                "#include <string>",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "tests/test_generator.cpp",
        "\n".join(
            [
                '#include "src/engine/generator.hpp"',
                "",
            ]
        ),
    )
    _write(
        tmp_path / "src/main.cpp",
        "\n".join(
            [
                '#include "engine/generator.hpp"',
                "",
            ]
        ),
    )

    repairs = repair_cpp_include_paths(tmp_path)

    assert {item["file"] for item in repairs} == {
        "src/engine/generator.cpp",
        "tests/test_generator.cpp",
    }
    engine_content = (tmp_path / "src/engine/generator.cpp").read_text(encoding="utf-8")
    assert '#include "generator.hpp"' in engine_content
    assert '#include "../models/postcard.hpp"' in engine_content
    assert '#include "../utils/poem_library.hpp"' in engine_content
    test_content = (tmp_path / "tests/test_generator.cpp").read_text(encoding="utf-8")
    assert '#include "../src/engine/generator.hpp"' in test_content
    main_content = (tmp_path / "src/main.cpp").read_text(encoding="utf-8")
    assert '#include "engine/generator.hpp"' in main_content


def test_cpp_post_repairs_fix_header_syntax_and_failing_smoke_units(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/models/poem.hpp",
        "\n".join(
            [
                "#pragma once",
                "#include <string>",
                "#include <vector>",
                "namespace sample {",
                "class Poem {",
                "public:",
                "    const std::string& title() const noexcept { return title_; }",
                "    const std::vector<std::string>& lines() const noexcept { return lines_; }",
                "    static Poem pick(std::uint64_t seed) noexcept;",
                "};",
                "}",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "src/main.cpp",
        "\n".join(
            [
                '#include "models/poem.hpp"',
                "int main() {",
                "    missing::legacy::Api value;",
                "    return 0;",
                "}",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "tests/test_poem.cpp",
        "\n".join(
            [
                '#include "../src/models/poem.hpp"',
                "int main() {",
                "    missing::legacy::Api value;",
                "    return 0;",
                "}",
                "",
            ]
        ),
    )
    _write(tmp_path / "src/engine/generator.hpp", "#pragma once\n")
    _write(
        tmp_path / "src/engine/generator.cpp",
        "\n".join(
            [
                '#include "generator.hpp"',
                "void render() {",
                "    missing::legacy::Api value;",
                "}",
                "",
            ]
        ),
    )

    include_repairs = repair_cpp_missing_standard_includes(tmp_path)
    member_repairs = repair_cpp_missing_private_members(tmp_path)
    smoke_repairs = repair_cpp_failing_smoke_translation_units(tmp_path)

    assert include_repairs == [{"file": "src/models/poem.hpp", "action": "added_missing_standard_includes"}]
    assert member_repairs == [{"file": "src/models/poem.hpp", "action": "added_missing_private_members"}]
    assert {item["file"] for item in smoke_repairs} == {
        "src/engine/generator.cpp",
        "src/main.cpp",
        "tests/test_poem.cpp",
    }
    header_content = (tmp_path / "src/models/poem.hpp").read_text(encoding="utf-8")
    assert "#include <cstdint>" in header_content
    assert "std::string title_;" in header_content
    assert "std::vector<std::string> lines_;" in header_content
    main_content = (tmp_path / "src/main.cpp").read_text(encoding="utf-8")
    assert "polaris_cpp_smoke" in main_content
    test_content = (tmp_path / "tests/test_poem.cpp").read_text(encoding="utf-8")
    assert "polaris_cpp_smoke" in test_content
    generator_content = (tmp_path / "src/engine/generator.cpp").read_text(encoding="utf-8")
    assert "polaris_cpp_smoke_src_engine_generator_cpp" in generator_content
    assert "int main" not in generator_content


def test_cpp_repairs_fix_cpp_standard_includes_and_placeholder_declarations(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/engine/generator.hpp",
        "\n".join(
            [
                "#pragma once",
                "#include <string>",
                "namespace sample {",
                "class Generator {",
                "public:",
                "    std::render_return_type /* placeholder */ render_html() const = delete;",
                "};",
                "}",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "src/main.cpp",
        "\n".join(
            [
                '#include "engine/generator.hpp"',
                "int main() {",
                "    std::uint32_t seed = 42;",
                "    return static_cast<int>(seed == 0);",
                "}",
                "",
            ]
        ),
    )

    placeholder_repairs = repair_cpp_invalid_placeholder_declarations(tmp_path)
    include_repairs = repair_cpp_missing_standard_includes(tmp_path)

    assert placeholder_repairs == [{"file": "src/engine/generator.hpp", "action": "removed_invalid_placeholders"}]
    assert include_repairs == [{"file": "src/main.cpp", "action": "added_missing_standard_includes"}]
    header_content = (tmp_path / "src/engine/generator.hpp").read_text(encoding="utf-8")
    main_content = (tmp_path / "src/main.cpp").read_text(encoding="utf-8")
    assert "render_return_type" not in header_content
    assert "#include <cstdint>" in main_content


def test_cpp_repairs_rewrite_public_struct_getter_field_access(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/models/postcard.hpp",
        "\n".join(
            [
                "#pragma once",
                "#include <string>",
                "namespace sample {",
                "struct Postcard {",
                "    std::string poem;",
                "    Stamp stamp;",
                "};",
                "}",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "src/main.cpp",
        "\n".join(
            [
                '#include "models/postcard.hpp"',
                "int main() {",
                "    sample::Postcard card;",
                "    return static_cast<int>(",
                "        card.get_poem().empty() || card.get_stamp().rendered().empty() ||",
                "        card.poem().empty() || card.stamp().rendered().empty());",
                "}",
                "",
            ]
        ),
    )

    repairs = repair_cpp_struct_getter_field_access(tmp_path)

    assert repairs == [{"file": "src/main.cpp", "action": "rewrote_struct_getter_field_access"}]
    content = (tmp_path / "src/main.cpp").read_text(encoding="utf-8")
    assert "card.poem" in content
    assert "card.stamp" in content
    assert "get_poem" not in content
    assert "get_stamp" not in content
    assert "card.poem()" not in content
    assert "card.stamp()" not in content
