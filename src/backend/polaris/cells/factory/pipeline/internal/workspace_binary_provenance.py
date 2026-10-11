"""Verify fixed HOST binary facts; these facts grant no execution authority.

The issuer pin is independent of the envelope. A component roundtrip, helper
ACK, or successful signature never admits an immutable worker deployment.
Production trust additionally requires HOST custody, live owner consumption,
revocation and physical drain. Unsupported Linux views fail closed.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import fcntl
import hashlib
import json
import os
import platform
import re
import select
import socket
import stat
import struct
import threading
import time
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOMAIN = b"paperclip.workspace-binary-provenance/v1\0"
CANONICAL_PATH = "/usr/bin/bwrap"
RELEASE_POLICY_PATH = Path("/etc/paperclip/polaris-validation-worker-policy-v1.json")
MAX_ENVELOPE = 16384
MAX_BINARY = 4 * 1024 * 1024
VIEW_POLICY = "immutable-consumer-self-thread-namespace-root-file-comparison"
MOUNT_POLICIES = {"statmount-unique-v1", "kernel-mountinfo-idmapped-v1"}
_BINDING_KEYS = {
    "ownerId",
    "companyId",
    "runId",
    "generation",
    "launchId",
    "agentId",
    "issueId",
    "processPid",
    "processStartedAt",
}


class ProvenanceError(RuntimeError):
    """A rejected fact, view or transport; never permits fallback execution."""


def require(value: object, code: str) -> None:
    if not value:
        raise ProvenanceError("workspace_binary_provenance_" + code)


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    require(len(items) == len({key for key, _ in items}), "duplicate_json_key")
    return dict(items)


def _nonfinite(value: str) -> Any:
    raise ProvenanceError("workspace_binary_provenance_nonfinite_json:" + value)


def decode_json(data: bytes, *, limit: int = MAX_ENVELOPE) -> dict[str, Any]:
    require(type(data) is bytes and 0 < len(data) <= limit, "json_size")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProvenanceError("workspace_binary_provenance_json_invalid") from exc
    if not isinstance(value, dict):
        raise ProvenanceError("workspace_binary_provenance_json_object_required")
    return value


def _fields(value: Any, names: set[str], code: str) -> None:
    require(type(value) is dict and set(value) == names, code)


def _integer(value: Any, *, minimum: int = 0, maximum: int = 2**53 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _decimal(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9]{1,24}", value) is not None


def _identifier(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) is not None


def _nonce(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _same(a: Any, b: Any) -> bool:
    # bool/int and int/float must not compare equal across a trust boundary.
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _base64(value: Any, *, maximum: int) -> bytes:
    require(type(value) is str and len(value) <= maximum * 2, "base64_size")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProvenanceError("workspace_binary_provenance_base64_invalid") from exc
    require(0 < len(data) <= maximum and base64.b64encode(data).decode("ascii") == value, "base64_noncanonical")
    return data


@dataclass(frozen=True)
class IssuerPin:
    """Operator release input, never taken from a proof, request or environment."""

    issuer_id: str
    epoch: str
    spki_der: bytes


@dataclass(frozen=True)
class ExpectedContext:
    """Expected HOST operation facts. This DTO is not an admission capability."""

    binding: Mapping[str, Any]
    nonce: str
    bootstrap_port_id: str
    challenge_id: str
    outer: Mapping[str, Any]
    expected_binary: Mapping[str, Any]
    mount_policy: str


@dataclass(frozen=True)
class VerifiedBinaryFacts:
    """Immutable signed bytes, not a Job Token, execution grant or receipt."""

    signed_bytes: bytes
    issuer_id: str
    issuer_epoch: str

    @property
    def payload(self) -> dict[str, Any]:
        return decode_json(self.signed_bytes[len(DOMAIN) :], limit=8192)


def _namespace(value: Any) -> None:
    _fields(value, {"device", "inode"}, "namespace_schema")
    require(_decimal(value["device"]) and _decimal(value["inode"]) and int(value["inode"]) > 0, "namespace_invalid")


def _outer(value: Any) -> None:
    _fields(
        value,
        {"pid", "start", "bootId", "uid", "gid", "userNamespace", "pidNamespace", "mountNamespace"},
        "outer_schema",
    )
    require(
        _integer(value["pid"], minimum=1) and _decimal(value["start"]) and _identifier(value["bootId"]), "outer_invalid"
    )
    require(_integer(value["uid"]) and _integer(value["gid"]), "outer_credentials_invalid")
    for name in ("userNamespace", "pidNamespace", "mountNamespace"):
        _namespace(value[name])


def _binding(value: Any) -> None:
    _fields(value, _BINDING_KEYS, "binding_schema")
    require(
        all(_integer(item, minimum=1) if key == "processPid" else _identifier(item) for key, item in value.items()),
        "binding_invalid",
    )


def _binary(value: Any) -> None:
    _fields(
        value,
        {"device", "inode", "size", "sha256", "uid", "gid", "mode", "nlink", "regular", "mountId"},
        "binary_schema",
    )
    require(all(_decimal(value[key]) for key in ("device", "inode", "size", "mountId")), "binary_identity_invalid")
    require(
        0 < int(value["size"]) <= MAX_BINARY and int(value["inode"]) > 0 and int(value["mountId"]) > 0,
        "binary_size_or_mount_invalid",
    )
    require(_nonce(value["sha256"]) and type(value["uid"]) is int and value["uid"] == 0, "binary_owner_invalid")
    require(_integer(value["gid"]) and _integer(value["mode"], maximum=0o7777), "binary_permissions_invalid")
    require(
        not value["mode"] & 0o7022
        and value["mode"] & 0o111
        and type(value["nlink"]) is int
        and value["nlink"] == 1
        and value["regular"] is True,
        "binary_permissions_invalid",
    )


def _observation(value: Any, context: ExpectedContext) -> None:
    _fields(
        value,
        {
            "version",
            "nonce",
            "bootId",
            "kernelCredentialScope",
            "kernelCredentialClaims",
            "referencedProcess",
            "outer",
            "namespaces",
            "root",
            "hostBinary",
            "peerBinary",
            "mountProof",
            "ancestry",
        },
        "observation_schema",
    )
    require(
        type(value["version"]) is int and value["version"] == 1 and value["nonce"] == context.nonce, "observation_nonce"
    )
    require(value["kernelCredentialScope"] == "kernel-validated-referenced-process", "credential_scope")
    _outer(value["outer"])
    require(_same(value["outer"], dict(context.outer)) and value["bootId"] == value["outer"]["bootId"], "outer_changed")
    referenced, claims = value["referencedProcess"], value["kernelCredentialClaims"]
    _fields(referenced, {"pid", "start"}, "referenced_schema")
    _fields(claims, {"pid", "uid", "gid"}, "credentials_schema")
    require(_integer(referenced["pid"], minimum=1) and _decimal(referenced["start"]), "referenced_invalid")
    require(
        _integer(claims["pid"], minimum=1) and _integer(claims["uid"]) and _integer(claims["gid"]),
        "credentials_invalid",
    )
    require(
        claims["pid"] == referenced["pid"]
        and claims["uid"] == value["outer"]["uid"]
        and claims["gid"] == value["outer"]["gid"],
        "credentials_changed",
    )
    _fields(value["namespaces"], {"user", "pid", "mount"}, "namespaces_schema")
    for item in value["namespaces"].values():
        _namespace(item)
    root = value["root"]
    _fields(root, {"device", "inode", "mountId"}, "root_schema")
    _namespace({key: root[key] for key in ("device", "inode")})
    require(_decimal(root["mountId"]) and int(root["mountId"]) > 0, "root_mount_invalid")
    for key in ("hostBinary", "peerBinary"):
        _binary(value[key])
        require(
            _same(
                {name: value[key][name] for name in ("device", "inode", "size", "sha256")},
                dict(context.expected_binary),
            ),
            "expected_binary_changed",
        )
    mount = value["mountProof"]
    _fields(mount, {"policy", "hostFilesystem", "peerFilesystem", "hostIdmapped", "peerIdmapped"}, "mount_schema")
    require(
        context.mount_policy in MOUNT_POLICIES
        and mount["policy"] == context.mount_policy
        and mount["hostFilesystem"] == mount["peerFilesystem"] == "ext4"
        and mount["hostIdmapped"] is False
        and mount["peerIdmapped"] is False,
        "mount_policy_unadmitted",
    )
    _fields(value["ancestry"], {"user", "pid"}, "ancestry_schema")
    require(value["ancestry"]["user"] is True and value["ancestry"]["pid"] is True, "ancestry_unverified")


def verify_envelope(
    data: bytes, pin: IssuerPin, context: ExpectedContext, *, now_ms: int | None = None
) -> VerifiedBinaryFacts:
    """Check independent Ed25519 pin, signed domain, exact schema and freshness."""
    envelope = decode_json(data)
    _fields(envelope, {"version", "issuerId", "algorithm", "signedBytes", "signature", "payload"}, "envelope_schema")
    require(
        type(envelope["version"]) is int and envelope["version"] == 1 and envelope["algorithm"] == "Ed25519",
        "envelope_version_or_algorithm",
    )
    require(
        _identifier(pin.issuer_id) and _identifier(pin.epoch) and envelope["issuerId"] == pin.issuer_id,
        "issuer_untrusted",
    )
    signed = _base64(envelope["signedBytes"], maximum=8192)
    signature = _base64(envelope["signature"], maximum=64)
    require(len(signature) == 64 and signed.startswith(DOMAIN), "signature_domain")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_der_public_key
    except ImportError as exc:
        raise ProvenanceError("workspace_binary_provenance_crypto_unavailable") from exc
    try:
        key = load_der_public_key(pin.spki_der)
        require(isinstance(key, Ed25519PublicKey), "pin_algorithm")
        key.verify(signature, signed)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ProvenanceError("workspace_binary_provenance_signature_invalid") from exc
    payload = decode_json(signed[len(DOMAIN) :], limit=8192)
    require(_same(payload, envelope["payload"]), "unsigned_payload_mismatch")
    _fields(
        payload,
        {
            "version",
            "issuerId",
            "purpose",
            "canonicalPath",
            "binding",
            "nonce",
            "issuedAt",
            "expiresAt",
            "consumerViewPolicy",
            "consumerContext",
            "observation",
        },
        "payload_schema",
    )
    require(
        type(payload["version"]) is int
        and payload["version"] == 1
        and payload["issuerId"] == pin.issuer_id
        and payload["purpose"] == "canonical-bwrap-ownership-facts"
        and payload["canonicalPath"] == CANONICAL_PATH,
        "payload_purpose",
    )
    _binding(payload["binding"])
    require(_same(payload["binding"], dict(context.binding)), "binding_changed")
    require(
        _nonce(context.nonce) and payload["nonce"] == context.nonce and payload["consumerViewPolicy"] == VIEW_POLICY,
        "nonce_or_view_policy",
    )
    _fields(payload["consumerContext"], {"bootstrapPortId", "challengeId"}, "consumer_context_schema")
    require(
        _identifier(context.bootstrap_port_id)
        and _identifier(context.challenge_id)
        and payload["consumerContext"]
        == {"bootstrapPortId": context.bootstrap_port_id, "challengeId": context.challenge_id},
        "consumer_context_changed",
    )
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    issued, expires = payload["issuedAt"], payload["expiresAt"]
    require(
        _integer(now)
        and _integer(issued)
        and _integer(expires)
        and 0 < expires - issued <= 1000
        and issued <= now < expires,
        "proof_expired",
    )
    _observation(payload["observation"], context)
    return VerifiedBinaryFacts(signed, pin.issuer_id, pin.epoch)


def _object(fd: int) -> dict[str, str]:
    metadata = os.fstat(fd)
    return {"device": str(metadata.st_dev), "inode": str(metadata.st_ino)}


def _start(path: str) -> str:
    data = Path(path).read_text(encoding="utf-8")
    fields = data[data.rfind(")") + 2 :].split()
    require(len(fields) > 19 and fields[0] not in {"Z", "X"} and _decimal(fields[19]), "process_dead")
    return fields[19]


def _namespaces(prefix: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, label in (("user", "user"), ("pid", "pid"), ("mnt", "mount")):
        fd = os.open(prefix + "/ns/" + name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            result[label] = _object(fd)
        finally:
            os.close(fd)
    return result


def capture_self_identity() -> dict[str, Any]:
    """Read actual self and native current thread, with before/after checks."""
    require(platform.system() == "Linux", "linux_required")
    tid = threading.get_native_id()
    start = _start("/proc/self/stat")
    thread_start = _start(f"/proc/self/task/{tid}/stat")
    namespaces = _namespaces("/proc/self")
    thread_namespaces = _namespaces(f"/proc/self/task/{tid}")
    root = os.stat("/proc/self/root")
    thread_root = os.stat(f"/proc/self/task/{tid}/root")
    require(
        namespaces == thread_namespaces and (root.st_dev, root.st_ino) == (thread_root.st_dev, thread_root.st_ino),
        "thread_view_changed",
    )
    require(
        start == _start("/proc/self/stat")
        and thread_start == _start(f"/proc/self/task/{tid}/stat")
        and namespaces == _namespaces("/proc/self"),
        "self_changed",
    )
    return {
        "pid": os.getpid(),
        "start": start,
        "bootId": Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip(),
        "namespaces": namespaces,
        "root": {"device": str(root.st_dev), "inode": str(root.st_ino)},
        "thread": {"pid": tid, "start": thread_start, "namespaces": thread_namespaces},
    }


def _mount_id(fd: int, *, unique: bool) -> int:
    require(platform.machine() in {"x86_64", "aarch64"}, "mount_platform_unsupported")
    libc = ctypes.CDLL(None, use_errno=True)
    require(hasattr(libc, "statx"), "mount_statx_unsupported")
    result = ctypes.create_string_buffer(256)
    mask = 0x4000 if unique else 0x1000
    require(
        libc.statx(
            ctypes.c_int(fd), ctypes.c_char_p(b""), ctypes.c_int(0x1000), ctypes.c_uint(mask), ctypes.byref(result)
        )
        == 0,
        "mount_statx_unsupported",
    )
    require(struct.unpack_from("I", result.raw)[0] & mask, "mount_id_unsupported")
    identity = struct.unpack_from("Q", result.raw, 0x90)[0]
    require(identity > 0, "mount_id_unverified")
    return int(identity)


def _mount_witness(fd: int, policy: str) -> dict[str, Any]:
    require(policy in MOUNT_POLICIES, "mount_policy_unadmitted")
    identity = _mount_id(fd, unique=policy == "statmount-unique-v1")
    libc = ctypes.CDLL(None, use_errno=True)
    if policy == "statmount-unique-v1":
        request = ctypes.create_string_buffer(struct.pack("IIQQ", 24, 0, identity, 0x22), 24)
        result = ctypes.create_string_buffer(4096)
        require(
            libc.syscall(
                ctypes.c_long(457), ctypes.byref(request), ctypes.byref(result), ctypes.c_size_t(4096), ctypes.c_uint(0)
            )
            == 0,
            "mount_statmount_unsupported",
        )
        data = result.raw
        used = struct.unpack_from("I", data)[0]
        mask = struct.unpack_from("Q", data, 8)[0]
        require(
            512 <= used <= 4096 and mask & 0x22 == 0x22 and struct.unpack_from("Q", data, 40)[0] == identity,
            "mount_result_unverified",
        )
        require(not struct.unpack_from("Q", data, 64)[0] & 0x00100000, "mount_idmapped_unsupported")
        offset = struct.unpack_from("I", data, 36)[0] + 512
        require(512 <= offset < used, "mount_filesystem_unverified")
        end = data.find(b"\0", offset, used)
        require(end >= 0 and data[offset:end] == b"ext4", "mount_filesystem_unsupported")
    else:
        # This policy requires independently operator-admitted kernel semantics
        # and HOST-held operation references; choosing it is never qualification.
        before = Path("/proc/thread-self/mountinfo").read_text(encoding="utf-8")
        require(0 < len(before.encode("utf-8")) <= 524288, "mountinfo_size")
        matches: list[tuple[list[str], list[str]]] = []
        for line in before.splitlines():
            fields = line.split(" - ")
            require(len(fields) == 2, "mountinfo_format")
            left, right = fields[0].split(), fields[1].split()
            require(len(left) >= 6 and len(right) == 3 and left[0].isdecimal(), "mountinfo_format")
            if int(left[0]) == identity:
                matches.append((left, right))
        require(len(matches) == 1, "mountinfo_identity_ambiguous")
        left, right = matches[0]
        options = left[5].split(",") + left[6:] + right[2].split(",")
        require("idmapped" not in options, "mount_idmapped_unsupported")
        known = {
            "ro",
            "rw",
            "nosuid",
            "nodev",
            "noexec",
            "noatime",
            "nodiratime",
            "relatime",
            "strictatime",
            "lazytime",
            "nosymfollow",
        }
        require(all(item in known for item in left[5].split(",")), "mount_options_unsupported")
        require(
            all(
                item == "unbindable"
                or any(
                    item.startswith(prefix) and item[len(prefix) :].isdecimal()
                    for prefix in ("shared:", "master:", "propagate_from:")
                )
                for item in left[6:]
            ),
            "mount_optional_fields_unsupported",
        )
        metadata = os.fstat(fd)
        require(
            right[0] == "ext4" and left[2] == f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}",
            "mount_device_or_filesystem_changed",
        )
        filesystem = ctypes.create_string_buffer(256)
        require(
            libc.fstatfs(ctypes.c_int(fd), ctypes.byref(filesystem)) == 0
            and ctypes.c_long.from_buffer(filesystem).value == 0xEF53,
            "mount_filesystem_unsupported",
        )
        require(
            before == Path("/proc/thread-self/mountinfo").read_text(encoding="utf-8")
            and _mount_id(fd, unique=False) == identity,
            "mountinfo_changed",
        )
    return {"mountId": str(identity), "filesystem": "ext4", "idmapped": False}


def _stamp(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_binary(root_fd: int, stack: ExitStack) -> int:
    cursor = root_fd
    for name in ("usr", "bin"):
        cursor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=cursor)
        stack.callback(os.close, cursor)
    fd = os.open("bwrap", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=cursor)
    stack.callback(os.close, fd)
    metadata = os.fstat(fd)
    require(
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and 0 < metadata.st_size <= MAX_BINARY,
        "self_binary_type_or_size",
    )
    return fd


def _file(fd: int, policy: str) -> dict[str, Any]:
    before = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while chunk := os.read(fd, 65536):
        total += len(chunk)
        require(total <= MAX_BINARY, "self_binary_size")
        digest.update(chunk)
    require(total == before.st_size, "self_binary_size_changed")
    mount = _mount_witness(fd, policy)
    require(_stamp(before) == _stamp(os.fstat(fd)), "self_binary_changed")
    return {
        "device": str(before.st_dev),
        "inode": str(before.st_ino),
        "size": str(before.st_size),
        "sha256": digest.hexdigest(),
        "mode": stat.S_IMODE(before.st_mode),
        "nlink": before.st_nlink,
        "mountId": mount["mountId"],
    }


def _descends(fd: int, outer: Mapping[str, str]) -> None:
    cursor = os.dup(fd)
    try:
        for _ in range(32):
            if _object(cursor) == dict(outer):
                return
            parent = fcntl.ioctl(cursor, 0xB702)
            os.close(cursor)
            cursor = parent
        raise ProvenanceError("workspace_binary_provenance_ancestry_unsupported")
    finally:
        os.close(cursor)


def compare_self_view(facts: VerifiedBinaryFacts) -> dict[str, Any]:
    """Re-read self/thread/root/file/outer from Linux; SCM claims are not self."""
    payload = facts.payload
    observation = payload["observation"]
    policy = observation["mountProof"]["policy"]
    before = capture_self_identity()
    require(
        {"pid": before["pid"], "start": before["start"]} == observation["referencedProcess"]
        and before["bootId"] == observation["bootId"]
        and before["namespaces"] == observation["namespaces"],
        "self_identity_mismatch",
    )
    outer = observation["outer"]
    with ExitStack() as stack:
        for name, label in (("user", "userNamespace"), ("pid", "pidNamespace")):
            fd = os.open("/proc/thread-self/ns/" + name, os.O_RDONLY | os.O_CLOEXEC)
            stack.callback(os.close, fd)
            _descends(fd, outer[label])
        outer_pidfd = os.pidfd_open(outer["pid"], 0)
        stack.callback(os.close, outer_pidfd)
        poll = select.poll()
        poll.register(outer_pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        require(not poll.poll(0) and _start(f"/proc/{outer['pid']}/stat") == outer["start"], "outer_process_changed")
        require(
            _namespaces(f"/proc/{outer['pid']}")
            == {"user": outer["userNamespace"], "pid": outer["pidNamespace"], "mount": outer["mountNamespace"]},
            "outer_namespace_changed",
        )
        root_fd = os.open("/proc/self/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        stack.callback(os.close, root_fd)
        thread_root = os.open("/proc/thread-self/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        stack.callback(os.close, thread_root)
        root = {**_object(root_fd), "mountId": str(_mount_id(root_fd, unique=policy == "statmount-unique-v1"))}
        require(
            root == observation["root"]
            and _object(thread_root) == _object(root_fd)
            and _mount_id(thread_root, unique=policy == "statmount-unique-v1") == int(root["mountId"]),
            "self_root_mismatch",
        )
        fd = _open_binary(root_fd, stack)
        witness = _file(fd, policy)
        expected = {key: observation["peerBinary"][key] for key in witness}
        require(witness == expected, "self_binary_mismatch")
        thread_file = _open_binary(thread_root, stack)
        require(
            _file(thread_file, policy) == witness and _stamp(os.fstat(thread_file)) == _stamp(os.fstat(fd)),
            "thread_binary_mismatch",
        )
        reopened = _open_binary(root_fd, stack)
        require(
            _file(reopened, policy) == witness and _stamp(os.fstat(reopened)) == _stamp(os.fstat(fd)),
            "binary_alias_changed",
        )
        require(
            capture_self_identity() == before
            and not poll.poll(0)
            and _start(f"/proc/{outer['pid']}/stat") == outer["start"],
            "self_or_outer_changed",
        )
        return {"self": before, "root": root, "binary": witness}


def consume_binary_facts(
    locator: str, pin: IssuerPin, context: ExpectedContext, *, timeout_seconds: float = 1.0
) -> VerifiedBinaryFacts:
    """One helper request/ACK/consumed exchange. Returned facts are non-authoritative."""
    require(
        type(locator) is str
        and locator.startswith("/")
        and "\0" not in locator
        and "\n" not in locator
        and len(os.fsencode(locator)) <= 103,
        "locator_invalid",
    )
    require(
        type(timeout_seconds) in {int, float} and 0 < timeout_seconds <= 1.0 and _nonce(context.nonce),
        "timeout_or_nonce_invalid",
    )
    deadline = time.monotonic() + timeout_seconds
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(timeout_seconds)
        connection.connect(locator)
        request = json.dumps({"version": 1, "nonce": context.nonce}, separators=(",", ":")).encode("ascii")
        require(connection.send(request) == len(request), "request_truncated")
        data, ancillary, flags, _ = connection.recvmsg(MAX_ENVELOPE + 1)
        require(not ancillary and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), "envelope_truncated")
        facts = verify_envelope(data, pin, context)
        compare_self_view(facts)
        require(
            time.monotonic() < deadline and time.time_ns() // 1_000_000 < facts.payload["expiresAt"], "proof_expired"
        )
        ack = json.dumps({"version": 1, "nonce": context.nonce, "ack": True}, separators=(",", ":")).encode("ascii")
        require(connection.send(ack) == len(ack), "ack_truncated")
        connection.settimeout(max(0.000001, deadline - time.monotonic()))
        confirmation, ancillary, flags, _ = connection.recvmsg(1025)
        require(not ancillary and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), "consumption_truncated")
        result = decode_json(confirmation, limit=1024)
        _fields(result, {"version", "nonce", "consumed"}, "consumption_schema")
        require(
            type(result["version"]) is int
            and result["version"] == 1
            and result["nonce"] == context.nonce
            and result["consumed"] is True,
            "consumption_denied",
        )
        require(
            time.monotonic() < deadline and time.time_ns() // 1_000_000 < facts.payload["expiresAt"], "proof_expired"
        )
        compare_self_view(facts)
        require(
            time.monotonic() < deadline and time.time_ns() // 1_000_000 < facts.payload["expiresAt"], "proof_expired"
        )
        return facts


class BinaryFactsConsumer:
    """Single attempt per operation context, including rejected/expired attempts.

    This local replay boundary supplements HOST single-consumption admission.
    Creating another instance never creates execution authority or HOST custody.
    """

    def __init__(self, pin: IssuerPin, context: ExpectedContext) -> None:
        self._pin = pin
        # Copy mutable DTO inputs before any blocking socket operation.
        self._context = ExpectedContext(
            json.loads(json.dumps(dict(context.binding))),
            context.nonce,
            context.bootstrap_port_id,
            context.challenge_id,
            json.loads(json.dumps(dict(context.outer))),
            json.loads(json.dumps(dict(context.expected_binary))),
            context.mount_policy,
        )
        self._lock = threading.Lock()
        self._consumed = False

    def consume(self, locator: str, *, timeout_seconds: float = 1.0) -> VerifiedBinaryFacts:
        with self._lock:
            require(not self._consumed, "request_consumed")
            self._consumed = True
        return consume_binary_facts(locator, self._pin, self._context, timeout_seconds=timeout_seconds)


def load_worker_release_policy() -> dict[str, Any]:
    """Read ONLY the fixed operator file. A policy is not immutable admission."""
    try:
        fd = os.open(RELEASE_POLICY_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        raise ProvenanceError("workspace_binary_provenance_release_policy_unavailable") from exc
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_nlink == 1
            and not before.st_mode & 0o022
            and 0 < before.st_size <= 16384,
            "release_policy_unprotected",
        )
        require(os.fstatvfs(fd).f_flag & os.ST_RDONLY, "release_policy_writable")
        data = os.read(fd, 16385)
        require(
            _stamp(before) == _stamp(os.fstat(fd))
            and _stamp(before) == _stamp(os.stat(RELEASE_POLICY_PATH, follow_symlinks=False)),
            "release_policy_changed",
        )
        policy = decode_json(data)
        _fields(
            policy,
            {"version", "issuerId", "issuerEpoch", "issuerSpkiDer", "mountEvidencePolicy", "closureManifestSha256"},
            "release_policy_schema",
        )
        require(
            type(policy["version"]) is int
            and policy["version"] == 1
            and _identifier(policy["issuerId"])
            and _identifier(policy["issuerEpoch"])
            and policy["mountEvidencePolicy"] in MOUNT_POLICIES
            and _nonce(policy["closureManifestSha256"]),
            "release_policy_invalid",
        )
        _base64(policy["issuerSpkiDer"], maximum=1024)
        return policy
    finally:
        os.close(fd)
