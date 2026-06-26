"""Regression: dependency export summaries carry enum members and class attrs.

factory-bench L1-03 (r07): ``src/engine/forecast.py`` referenced
``SkyCondition.CLEAR`` but the ``SkyCondition`` enum only defined
``CALM/FAIR/CLOUDY/WINDY/STORMY`` (``CLEAR`` lived on a sibling enum). The
entrypoint crashed with
``AttributeError: type object 'SkyCondition' has no attribute 'CLEAR'``.

Root cause: the cross-file dependency signature injected into the Director listed
only ``class SkyCondition(Enum):`` — not its members — so the Director guessed a
plausible-but-non-existent member. The export summary must enumerate enum members
(and class attributes) so dependent files reference real symbols.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor


def _executor(workspace: Path) -> OrchestrationStageExecutor:
    return OrchestrationStageExecutor(workspace)


class TestPyExportSummaryEnumMembers:
    def test_enum_members_are_included(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary(
            "from enum import Enum\n\n\n"
            "class SkyCondition(Enum):\n"
            "    CALM = 'calm'\n"
            "    FAIR = 'fair'\n"
            "    CLOUDY = 'cloudy'\n"
            "    WINDY = 'windy'\n"
            "    STORMY = 'stormy'\n"
        )
        assert "class SkyCondition(Enum):" in summary
        for member in ("CALM", "FAIR", "CLOUDY", "WINDY", "STORMY"):
            assert member in summary
        # The hallucinated member that crashed r07 must NOT be implied as valid.
        assert "CLEAR" not in summary

    def test_sibling_enums_keep_distinct_members(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary(
            "from enum import Enum\n\n\n"
            "class SkyCondition(Enum):\n    CALM = 'calm'\n    STORMY = 'stormy'\n\n\n"
            "class Weather(Enum):\n    CLEAR = 'clear'\n    RAINY = 'rainy'\n"
        )
        assert "class SkyCondition(Enum): members: CALM, STORMY" in summary
        assert "class Weather(Enum): members: CLEAR, RAINY" in summary

    def test_function_signature_includes_arg_names(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary(
            "def derive_mood(weather, *, intensity=0.5):\n    return weather\n"
        )
        assert "def derive_mood(weather, intensity)" in summary

    def test_dataclass_attributes_are_included(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary(
            "from dataclasses import dataclass\n\n\n@dataclass\nclass WeatherReport:\n    sky: str\n    temperature: float = 0.0\n"
        )
        assert "class WeatherReport:" in summary
        assert "sky" in summary
        assert "temperature" in summary

    def test_intenum_base_is_treated_as_enum(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary(
            "from enum import IntEnum\n\n\nclass Level(IntEnum):\n    LOW = 1\n    HIGH = 2\n"
        )
        assert "class Level(IntEnum): members: LOW, HIGH" in summary

    def test_unparseable_source_falls_back_without_raising(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary("class Broken(:\n    def method(self)\n")
        assert isinstance(summary, str)

    def test_module_level_constants_are_included(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_py_export_summary(
            "MAX_WIND = 120\n\n\ndef gust():\n    return MAX_WIND\n"
        )
        assert "MAX_WIND = ..." in summary
        assert "def gust()" in summary


class TestJsExportSummaryMembers:
    """JS/TS analog of the enum-member gap (L4-L8 cross-file coherence)."""

    def test_ts_enum_members_are_included(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_js_export_summary(
            "export enum SkyCondition {\n  CALM = 'calm',\n  FAIR = 'fair',\n  CLOUDY = 'cloudy',\n  STORMY = 'stormy',\n}\n"
        )
        assert "enum SkyCondition" in summary
        for member in ("CALM", "FAIR", "CLOUDY", "STORMY"):
            assert member in summary
        assert "CLEAR" not in summary

    def test_const_enum_members_are_included(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_js_export_summary("export const enum Level { LOW, HIGH }\n")
        assert "enum Level { LOW, HIGH }" in summary

    def test_interface_and_type_are_captured(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_js_export_summary(
            "export interface WeatherReport {\n  sky: string;\n}\n\nexport type Mood = 'bright' | 'stormy';\n"
        )
        assert "interface WeatherReport" in summary
        assert "type Mood" in summary

    def test_non_arrow_const_export_is_captured(self, tmp_path: Path) -> None:
        # The old extractor only matched ``const x = (`` (arrow fns); plain value
        # exports like a const array were missed.
        summary = _executor(tmp_path)._extract_js_export_summary(
            "export const CONTRACT_KEYWORDS = ['planet', 'weather'];\n"
        )
        assert "CONTRACT_KEYWORDS" in summary

    def test_named_export_list_is_captured(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_js_export_summary("export { broadcast, broadcastMany };\n")
        assert "export {" in summary and "broadcast" in summary

    def test_class_and_function_still_captured(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_js_export_summary(
            "export class RadioBroadcaster {}\n\nexport async function broadcast(report) {\n  return report;\n}\n"
        )
        assert "class RadioBroadcaster" in summary
        assert "function broadcast" in summary

    def test_commonjs_exports_still_captured(self, tmp_path: Path) -> None:
        summary = _executor(tmp_path)._extract_js_export_summary(
            "function derive() {}\nmodule.exports = { derive };\nexports.derive = derive;\n"
        )
        assert "module.exports" in summary
