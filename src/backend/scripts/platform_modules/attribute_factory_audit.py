#!/usr/bin/env python3
"""Attribute factory_audits.json residuals to one platform module_id.

Usage:
  python src/backend/scripts/platform_modules/attribute_factory_audit.py \\
      --audits /path/to/factory_audits.json \\
      --json-out /tmp/attribution.json

Workflow supervisors may consume the non-terminal
``primary.primary_module_id`` candidate. Does not schedule, repair, or bench.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audits", required=True, help="Path to factory_audits.json")
    parser.add_argument("--json-out", default="", help="Optional write path for attribution pack")
    args = parser.parse_args(argv)

    audits_path = Path(args.audits).expanduser().resolve()
    if not audits_path.is_file():
        print(f"audits not found: {audits_path}", file=sys.stderr)
        return 2

    pack = attribute_factory_audits_file(str(audits_path))
    out = {
        "schema_version": "platform.residual_attribution_pack.v1",
        "attribution_pack": pack,
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
