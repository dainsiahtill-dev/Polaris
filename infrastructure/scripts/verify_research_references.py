"""Internal evidence audit CLI. Use PYTHONPATH=src/backend from the Polaris checkout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from polaris.cells.roles.runtime.public.engineering_research import decode_research_json, verify_references


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--query", required=True, type=Path, help="UTF-8 JSON with a reports list")
    parser.add_argument("--recorded-windows-root", help="Original Windows spelling of the same authorized root")
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024, help="Maximum bytes per source or artifact")
    args = parser.parse_args()
    try:
        query = decode_research_json(args.query.read_bytes())
        if not isinstance(query, dict) or query.get("schema") != "research.reference-query.v1":
            raise ValueError("expected research.reference-query.v1")
        result = verify_references(
            args.root, query.get("reports"), max_bytes=args.max_bytes, recorded_windows_root=args.recorded_windows_root
        )
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "unproven", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
