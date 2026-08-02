#!/usr/bin/env python3
"""Attribute factory_audits.json residuals to one platform module_id.

Usage:
  python src/backend/scripts/platform_modules/attribute_factory_audit.py \\
      --audits /path/to/factory_audits.json \\
      --json-out /tmp/attribution.json

Unattended supervisors consume the JSON primary.primary_module_id and
gate_commands. Does not run repairs or benches.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from polaris.kernelone.platform_modules.residual_attribution import (  # noqa: E402
    attribute_factory_audits_file,
)
from polaris.kernelone.platform_modules.unattended_supervisor import (  # noqa: E402
    plan_unattended_step,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audits", required=True, help="Path to factory_audits.json")
    parser.add_argument("--json-out", default="", help="Optional write path for attribution pack")
    parser.add_argument(
        "--cascade-ok",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Known cascade readiness for next-step plan",
    )
    parser.add_argument(
        "--module-gate-ok",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Known module-gate readiness for next-step plan",
    )
    parser.add_argument("--n-batch-streak", type=int, default=0)
    args = parser.parse_args(argv)

    audits_path = Path(args.audits).expanduser().resolve()
    if not audits_path.is_file():
        print(f"audits not found: {audits_path}", file=sys.stderr)
        return 2

    pack = attribute_factory_audits_file(str(audits_path))
    step = plan_unattended_step(
        attribution=pack["primary"],
        cascade_ok=args.cascade_ok,
        module_gate_ok=args.module_gate_ok,
        n_batch_streak=int(args.n_batch_streak or 0),
    )
    out = {
        "schema_version": "platform.unattended_attribution_pack.v1",
        "attribution_pack": pack,
        "next_step": step.to_dict(),
    }
    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if args.json_out:
        out_path = Path(args.json_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
