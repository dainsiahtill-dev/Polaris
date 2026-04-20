import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

from polaris.infrastructure.db.adapters import LanceDbAdapter
from polaris.kernelone.db import KernelDatabase


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def ensure_record(value):
    if isinstance(value, list):
        return [ensure_record(item) for item in value]
    if not isinstance(value, dict):
        return {"id": str(uuid.uuid4()), "value": value}
    data = dict(value)
    data.setdefault("id", str(uuid.uuid4()))
    data.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    return data


def normalize_db_dir(path: str, workspace: str = "") -> str:
    raw = (path or "").strip().strip('"')

    if re.match(r"^[A-Za-z]:$", raw):
        raw = raw + "\\"
    raw = os.path.abspath(raw)
    if re.match(r"^[A-Za-z]:$", raw):
        raw = raw + "\\"

    if re.match(r"^[A-Za-z]:\\?$", raw):
        if workspace:
            try:
                from polaris.kernelone.storage import resolve_workspace_persistent_path

                raw = resolve_workspace_persistent_path(workspace, "workspace/lancedb")
            except Exception:
                raw = os.path.join(raw, ".polaris", "lancedb")
        else:
            raw = os.path.join(raw, ".polaris", "lancedb")

    if os.name == "nt":
        if re.match(r"^[A-Za-z]:\\", raw) and not raw.startswith("\\\\?\\"):
            raw = "\\\\?\\" + raw
        elif raw.startswith("\\\\") and not raw.startswith("\\\\?\\"):
            raw = "\\\\?\\UNC\\" + raw.lstrip("\\")
    return raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--json", required=True)
    parser.add_argument("--workspace", default="")
    args = parser.parse_args()

    if not os.path.exists(args.json):
        print("json not found", file=sys.stderr)
        return 0

    workspace_root = str(args.workspace or os.getcwd())
    db_dir = normalize_db_dir(args.db, workspace=workspace_root)
    kernel_db = KernelDatabase(
        workspace_root,
        lancedb_adapter=LanceDbAdapter(),
        allow_unmanaged_absolute=True,
    )
    try:
        db_dir = kernel_db.resolve_lancedb_path(db_dir, ensure_exists=True)
    except Exception as exc:
        print(f"failed to create lancedb dir: {db_dir}: {exc}", file=sys.stderr)
        return 0

    payload = load_json(args.json)
    records = ensure_record(payload)
    if isinstance(records, dict):
        records = [records]

    try:
        db = kernel_db.lancedb(db_dir, ensure_exists=True)
    except Exception as exc:
        if "not installed" in str(exc).lower():
            print(f"lancedb not installed; skipping (python={sys.executable})", file=sys.stderr)
            print(f"lancedb import error: {exc}", file=sys.stderr)
            return 0
        print(f"lancedb connect failed for {db_dir}: {exc}", file=sys.stderr)
        return 0

    table_name = "codex_memory"
    try:
        table = db.open_table(table_name)
        table.add(records)
    except Exception:
        try:
            db.create_table(table_name, data=records)
        except Exception as exc:
            print(f"lancedb create_table failed for {db_dir}: {exc}", file=sys.stderr)
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
