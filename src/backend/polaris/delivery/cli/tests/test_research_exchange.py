from argparse import Namespace
from pathlib import Path
from typing import Any

import httpx
import pytest
from polaris.delivery import research_peer
from polaris.delivery.cli import research_exchange


@pytest.mark.parametrize("wrong_origin", [False, True])
def test_transfer_checks_both_receipt_and_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong_origin: bool
) -> None:
    source_token = tmp_path / "source-token"
    source_token.write_text("sourceSecret", encoding="utf-8")
    consumer_token = tmp_path / "consumer-token"
    consumer_token.write_text("consumerSecret", encoding="utf-8")
    peers = {
        "A": {"workspace": "/source", "backend_url": "http://127.0.0.1:9001"},
        "C": {"workspace": "/consumer", "backend_url": "http://127.0.0.1:9002"},
    }
    monkeypatch.setattr(research_peer, "registered_research_instance", lambda key, home=None: peers[key])
    digest = "b" * 64
    receipt = {
        "publication_sha256": digest,
        "producer": {"producer_workspace": "/different" if wrong_origin else "/source", "run_id": "r1"},
        "report": {"manifest": f"research-inputs/{digest}/manifest.json", "sha256": "a" * 64},
    }

    def observe(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:9002/v2/engineering/research/imports"
        assert request.headers["Authorization"] == "Bearer consumerSecret"
        assert request.headers["X-Polaris-Research-Source-Token"] == "sourceSecret"
        assert b"Secret" not in request.content
        return httpx.Response(200, json=receipt)

    original = httpx.Client

    def client(**options: Any) -> httpx.Client:
        assert options["trust_env"] is False and options["follow_redirects"] is False
        return original(**options, transport=httpx.MockTransport(observe))

    monkeypatch.setattr(httpx, "Client", client)
    command = Namespace(
        action="transfer",
        source_run="r1",
        source_instance="A",
        consumer_instance="C",
        registry_home=None,
        source_token_file=source_token,
        consumer_token_file=consumer_token,
        publication=digest,
        files=[],
        verification_seq=None,
        experiment=None,
        permission_profile_id="lab",
    )
    if wrong_origin:
        with pytest.raises(ValueError, match="receipt"):
            research_exchange._run(command)
    else:
        assert research_exchange._run(command) == receipt
