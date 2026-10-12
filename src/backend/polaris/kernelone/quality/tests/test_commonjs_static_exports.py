"""Simple CommonJS outputs have a known export surface before runnable gates."""

from pathlib import Path

import pytest
from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence


@pytest.mark.parametrize("symbol", ["value", "missing"])
def test_commonjs_assignment_exports_are_checked_without_executing_code(tmp_path: Path, symbol: str) -> None:
    files = {
        "dist/value.js": '"use strict";\nObject.defineProperty(exports, "__esModule", { value: true });\n'
        "exports.value = value;\nfunction value() { return 7; }\n",
        "tests/value.test.ts": f"import {{ {symbol} }} from '../dist/value.js';\nconsole.log({symbol});\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=["tests/value.test.ts"], scan_mode="pure_static"
    )

    assert not any("import_export_surface_unknown" in reason for reason in evidence.incomplete_reasons)
    assert any("unresolved import symbol" in error for error in evidence.errors) is (symbol == "missing")
    assert (tmp_path / "dist/value.js").read_text(encoding="utf-8") == files["dist/value.js"]


def test_dynamic_commonjs_exports_remain_unknown(tmp_path: Path) -> None:
    (tmp_path / "value.js").write_text("module.exports = chooseRuntimeShape();\n", encoding="utf-8")
    (tmp_path / "test.ts").write_text("import { value } from './value.js';\n", encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=["test.ts"], scan_mode="pure_static"
    )

    assert any("import_export_surface_unknown" in reason for reason in evidence.incomplete_reasons)
