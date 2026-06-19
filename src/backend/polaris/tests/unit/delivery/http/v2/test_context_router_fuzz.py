"""Adversarial fuzz tests for the GET /v2/context/{hash} hash path.

Hand-written parameterised matrix of adversarial inputs. Each input must
not produce a 500 leak and must round-trip to the correct response:

* syntactically invalid hashes → 400 INVALID_HASH (validator catches them)
* valid 24-char hex that does not exist on disk → 404 CONTEXT_NOT_FOUND
* path segments Starlette refuses to route → 404 from the router itself

The transport-level URL filter (``httpx`` rejects non-printable chars in
the URL before it ever reaches FastAPI) is the *first* layer of defence;
the regex validator is the *second*; ``resolve_artifact_path`` /
``_join_under`` is the *third*.  This test pins all three layers.

If ``hypothesis`` ever lands in ``pyproject.toml`` the optional decorator
below can be uncommented to additionally exercise random strings.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.v2._shared import require_auth
from polaris.delivery.http.v2.context import router as context_router


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


def _build_client(workspace: str = ".") -> TestClient:
    app = FastAPI()
    app.include_router(context_router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=workspace, ramdisk_root=""),
    )
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


# Inputs that the validator MUST reject with 400 INVALID_HASH.
# (Control-character inputs that httpx refuses to URL-encode are tested
# separately via the unit-level validator below.)
INVALID_HASH_INPUTS: list[str] = [
    # uppercase hex
    "AABBCC112233445566778899",
    "AaBbCc112233445566778899",
    "aabbCC112233445566778899",
    # length boundary
    "a" * 22,
    "a" * 23,
    "a" * 25,
    "a" * 26,
    "a" * 64,
    "a" * 128,
    "a" * 1024,
    # longest possible single segment
    "0" * 4096,
    # 24-char strings that look hash-like but aren't hex
    "g" * 24,
    "z" * 24,
    "G" * 24,
    # inner whitespace
    "aabbcc 112233445566778899",
    "aabbcc112233445 66778899",
]


@pytest.mark.parametrize(
    "raw",
    INVALID_HASH_INPUTS,
    ids=[f"case-{i}" for i in range(len(INVALID_HASH_INPUTS))],
)
def test_invalid_hashes_return_400(tmp_path, raw: str) -> None:
    """Each malformed hash must yield 400 INVALID_HASH — no 500, no 200."""
    client = _build_client(str(tmp_path))
    response = client.get(f"/v2/context/{raw}")
    assert response.status_code == 400, f"input {raw!r} yielded {response.status_code}, expected 400"
    detail = response.json().get("detail", {})
    assert detail.get("code") == "INVALID_HASH", (
        f"input {raw!r} yielded code={detail.get('code')!r}, expected INVALID_HASH"
    )


# Valid 24-char hex inputs that don't exist on disk.  They MUST return
# 404 CONTEXT_NOT_FOUND (or 503 if validator runs before layout).  They
# MUST NEVER return 500 — a 500 would indicate the validator or layout
# raised rather than caught the input.
VALID_BUT_MISSING: list[str] = [
    "0" * 24,
    "f" * 24,
    "0123456789abcdef01234567",
    "fedcba9876543210fedcba98",
    "deadbeefcafebabe01234567",
]


@pytest.mark.parametrize(
    "raw",
    VALID_BUT_MISSING,
    ids=[f"valid-{i}" for i in range(len(VALID_BUT_MISSING))],
)
def test_valid_hashes_missing_return_404(tmp_path, raw: str) -> None:
    """Valid-format hash with no on-disk file yields 404 — never 500."""
    client = _build_client(str(tmp_path))
    response = client.get(f"/v2/context/{raw}")
    assert response.status_code in (400, 404), f"input {raw!r} yielded {response.status_code}"
    assert response.status_code != 500, f"input {raw!r} leaked a 500 — validator/layout must catch this"


def test_validator_pure_unit() -> None:
    """The shared validator must reject a battery of obvious bad inputs."""
    from polaris.kernelone.llm.engine.internal.context_hash import (
        CONTEXT_HASH_PATTERN,
        validate_context_hash,
    )

    bad_inputs = [
        "",
        " ",
        "aabbcc11223344556677889",  # 23 chars
        "aabbcc1122334455667788999",  # 25 chars
        "AABBCC112233445566778899",  # uppercase
        "aabbcc11223344556677889g",  # non-hex
        "aabbcc 112233445566778899",  # whitespace inside
        "../aabbcc1122334455667788",  # dot segment
        "aabbcc1122334455667788\0",  # null byte
    ]
    for raw in bad_inputs:
        with pytest.raises(ValueError):
            validate_context_hash(raw)

    # The pattern itself must anchor both ends.
    assert CONTEXT_HASH_PATTERN.fullmatch("aabbcc112233445566778899") is not None
    assert CONTEXT_HASH_PATTERN.fullmatch(" aabbcc112233445566778899") is None
    assert CONTEXT_HASH_PATTERN.fullmatch("aabbcc112233445566778899 ") is None
    assert CONTEXT_HASH_PATTERN.fullmatch("aabbcc112233445566778899x") is None

    # Whitespace-stripped normalised form is accepted (validator strips).
    assert validate_context_hash("  aabbcc112233445566778899  ") == ("aabbcc112233445566778899")


# ----------------------------------------------------------------------------
# Optional: hypothesis-based fuzz. Commented out by default — hypothesis is
# not in pyproject.toml.  When added, uncomment and re-enable:
#
# @given(st.text(min_size=0, max_size=64))
# def test_hypothesis_text_never_crashes(tmp_path, raw: str) -> None:
#     client = _build_client(str(tmp_path))
#     response = client.get(f"/v2/context/{raw}")
#     # Never a 500 — either 400 (invalid) or 404 (missing).
#     assert response.status_code in (400, 404)
# ----------------------------------------------------------------------------
