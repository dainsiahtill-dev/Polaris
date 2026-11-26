"""Host-operated exchange of exact research publications between registered instances."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


def _token(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("invalid credential file")
    token = raw.decode("utf-8").strip()
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("credential file must contain one bearer token")
    return token


def _response(response: httpx.Response) -> dict[str, Any]:
    if not response.is_success:
        raise ValueError(f"research owner returned HTTP {response.status_code}")
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("invalid research owner response")
    return result


def _run(args: argparse.Namespace) -> dict[str, Any]:
    from polaris.cells.roles.runtime.public.engineering_research import (
        PLAN_LIMIT,
        adopt_research_publications,
        decode_research_json,
        valid_digest,
        validate_descriptor,
    )
    from polaris.delivery.research_peer import registered_research_instance

    if args.action == "adopt":
        with args.plan.open("rb") as stream:
            raw = stream.read(PLAN_LIMIT + 1)
        if len(raw) > PLAN_LIMIT:
            raise ValueError("research plan exceeds 2 MiB")
        plan = decode_research_json(raw)
        return adopt_research_publications(
            args.workspace.resolve(strict=True), plan, args.publication, args.for_experiment
        )
    if args.source_run in {"", ".", ".."} or any(ch in "/\\" or ord(ch) < 32 for ch in args.source_run):
        raise ValueError("invalid producer run identity")
    source = registered_research_instance(args.source_instance, home=args.registry_home)
    source_token = _token(args.source_token_file)
    consumer = None
    consumer_token = ""
    if args.action == "transfer":
        consumer = registered_research_instance(args.consumer_instance, home=args.registry_home)
        if consumer["workspace"] == source["workspace"]:
            raise ValueError("select two independent registered workspaces")
        consumer_token = _token(args.consumer_token_file)
    digest = getattr(args, "publication", None)
    if digest is not None and not valid_digest(digest):
        raise ValueError("invalid publication SHA-256")
    if digest and (args.files or args.experiment or args.verification_seq is not None):
        raise ValueError("an existing publication already fixes its experiment, verification and files")
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=180) as client:
        if not digest:
            if not args.experiment or not args.files:
                raise ValueError("creating a publication requires --experiment and at least one --file")
            response = _response(
                client.post(
                    source["backend_url"]
                    + "/v2/engineering/runs/"
                    + quote(args.source_run, safe="")
                    + "/research-publications",
                    headers={"Authorization": "Bearer " + source_token},
                    json={
                        "workspace": source["workspace"],
                        "experiment": args.experiment,
                        "files": args.files,
                        "verification_seq": args.verification_seq,
                    },
                )
            )
            digest = response.get("publication_sha256")
            descriptor = response.get("descriptor")
            if not isinstance(digest, str) or not isinstance(descriptor, dict):
                raise ValueError("producer did not issue a publication")
            validate_descriptor(descriptor, digest)
            if descriptor["producer_workspace"] != source["workspace"] or descriptor["run_id"] != args.source_run:
                raise ValueError("producer response does not match the selected run and workspace")
            if (
                descriptor["experiment"] != args.experiment
                or [row["path"] for row in descriptor["files"]] != sorted(args.files)
                or (args.verification_seq is not None and descriptor["verification_seq"] != args.verification_seq)
            ):
                raise ValueError("producer response does not match the requested experiment, files or verification")
            if args.action == "publish":
                return response
        assert consumer is not None
        result = _response(
            client.post(
                consumer["backend_url"] + "/v2/engineering/research/imports",
                headers={"Authorization": "Bearer " + consumer_token, "X-Polaris-Research-Source-Token": source_token},
                json={
                    "workspace": consumer["workspace"],
                    "source_instance": args.source_instance,
                    "source_run": args.source_run,
                    "publication_sha256": digest,
                    "permission_profile_id": args.permission_profile_id,
                },
            )
        )
        producer = result.get("producer")
        report = result.get("report")
        if (
            result.get("publication_sha256") != digest
            or not isinstance(producer, dict)
            or producer.get("producer_workspace") != source["workspace"]
            or producer.get("run_id") != args.source_run
            or not isinstance(report, dict)
            or not valid_digest(report.get("sha256"))
            or report.get("manifest") != f"research-inputs/{digest}/manifest.json"
        ):
            raise ValueError("consumer receipt does not match the selected publication")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("publish", "transfer"):
        p = commands.add_parser(action)
        p.add_argument("--registry-home", type=Path)
        p.add_argument("--source-instance", required=True)
        p.add_argument("--source-run", required=True)
        p.add_argument("--source-token-file", type=Path, required=True)
        p.add_argument("--experiment")
        p.add_argument("--file", dest="files", action="append", default=[])
        p.add_argument("--verification-seq", type=int)
        if action == "transfer":
            p.add_argument("--publication", help="SHA of an already issued producer publication")
            p.add_argument("--consumer-instance", required=True)
            p.add_argument("--consumer-token-file", required=True, type=Path)
            p.add_argument("--permission-profile-id")
    adopt = commands.add_parser("adopt")
    adopt.add_argument("--workspace", type=Path, required=True)
    adopt.add_argument("--plan", type=Path, required=True)
    adopt.add_argument("--publication", action="append", required=True)
    adopt.add_argument(
        "--for-experiment", action="append", default=[], help="Consumer lab/id to rebind; repeat for multiple consumers"
    )
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            result = _run(args)
    except (OSError, KeyError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(json.dumps({"status": "unproven", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
