"""P3 deterministic file-assembly merger — codex TDD order: failure cases first."""

from __future__ import annotations

from polaris.kernelone.quality.assembly_merger import (
    ASSEMBLY_DUPLICATE_ANCHOR,
    ASSEMBLY_INTERFACE_DRIFT,
    ASSEMBLY_MISSING_ANCHOR,
    ASSEMBLY_OUT_OF_REGION,
    anchor_names,
    validate_fill_assembly,
)

# Skeleton baseline (interface LAW): complete shell + one @anchor per function body.
BASELINE = """import { CONFIG } from './config.js';
export const STATE = {};
function init() {
  // @anchor:init
}
function update() {
  // @anchor:update
}
"""

OWNS_INIT = ["init"]


class TestRejections:
    """Every contract violation is REJECTED (codex: change sig / import-export / delete
    anchor / touch unassigned func / whole-file rewrite / missing anchor / dup anchor)."""

    def test_changed_signature_is_interface_drift(self) -> None:
        proposed = BASELINE.replace("function init() {", "function init(extra) {")
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_INTERFACE_DRIFT

    def test_changed_import_is_interface_drift(self) -> None:
        proposed = BASELINE.replace(
            "import { CONFIG } from './config.js';", "import { CONFIG, EXTRA } from './config.js';"
        )
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_INTERFACE_DRIFT

    def test_changed_export_const_is_interface_drift(self) -> None:
        proposed = BASELINE.replace("export const STATE = {};", "export const STATE = {score: 0};")
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_INTERFACE_DRIFT

    def test_deleted_anchor_is_missing(self) -> None:
        proposed = BASELINE.replace("  // @anchor:update\n", "")
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_MISSING_ANCHOR

    def test_modified_unassigned_function_is_out_of_region(self) -> None:
        # The fill owns only 'init' but also implements 'update' (unassigned).
        proposed = BASELINE.replace(
            "function update() {\n  // @anchor:update\n}",
            "function update() {\n  // @anchor:update\n  doStuff();\n}",
        )
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_OUT_OF_REGION

    def test_whole_file_rewrite_drops_anchors_is_missing(self) -> None:
        proposed = "function init() {\n  return 1;\n}\nfunction update() {\n  return 2;\n}\n"
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_MISSING_ANCHOR

    def test_duplicate_anchor_is_rejected(self) -> None:
        proposed = BASELINE.replace("  // @anchor:init\n", "  // @anchor:init\n  // @anchor:init\n")
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert not v.ok and v.error_code == ASSEMBLY_DUPLICATE_ANCHOR


class TestAcceptance:
    """A fill that implements ONLY its owned anchor and keeps the interface snapshot is
    accepted; the merged result (== proposed) is then syntactically valid."""

    def test_owned_body_only_is_accepted(self) -> None:
        proposed = BASELINE.replace(
            "function init() {\n  // @anchor:init\n}",
            "function init() {\n  // @anchor:init\n  STATE.ready = true;\n  return CONFIG;\n}",
        )
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert v.ok, v.message

    def test_keeping_the_anchor_marker_is_not_a_diff(self) -> None:
        # update stays a stub (keeps its marker) → unchanged unassigned body → OK.
        proposed = BASELINE.replace(
            "function init() {\n  // @anchor:init\n}",
            "function init() {\n  // @anchor:init\n  return 1;\n}",
        )
        v = validate_fill_assembly(BASELINE, proposed, owned_anchors=OWNS_INIT)
        assert v.ok

    def test_python_def_skeleton_round_trips(self) -> None:
        base = "import os\ndef init():\n    # @anchor:init\n    pass\ndef run():\n    # @anchor:run\n    pass\n"
        proposed = base.replace("    # @anchor:init\n    pass", "    # @anchor:init\n    return os.getcwd()")
        v = validate_fill_assembly(base, proposed, owned_anchors=["init"])
        assert v.ok, v.message


def test_anchor_names_in_order() -> None:
    assert anchor_names(BASELINE) == ["init", "update"]
