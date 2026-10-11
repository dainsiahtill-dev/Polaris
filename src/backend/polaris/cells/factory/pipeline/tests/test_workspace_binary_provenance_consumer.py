"""Component tests only; temporary issuers never qualify a deployment."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest.mock import patch

MODULE = "polaris.cells.factory.pipeline.internal.workspace_binary_provenance"

# Explicit component probe loads the exact source file without importing the
# whole Factory closure into the unrelated system interpreter. No source copy,
# dependency installation, sys.path crypto fallback or production admission.
if "--component-probe" in sys.argv:
    from pathlib import Path

    sys.argv.remove("--component-probe")
    spec = importlib.util.spec_from_file_location(
        MODULE, Path(__file__).parents[1] / "internal/workspace_binary_provenance.py"
    )
    assert spec is not None and spec.loader is not None
    component = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = component
    spec.loader.exec_module(component)


class ConsumerTests(unittest.TestCase):
    def test_failed_first_consumption_burns_nonce_no_retry(self) -> None:
        consumer = importlib.import_module(MODULE)
        context = consumer.ExpectedContext({}, "a" * 64, "port", "challenge", {}, {}, "statmount-unique-v1")
        request = consumer.BinaryFactsConsumer(consumer.IssuerPin("unit-issuer", "epoch", b"not-a-key"), context)
        with self.assertRaises(OSError):
            request.consume("/nonexistent-polaris-unit-socket")
        with self.assertRaisesRegex(consumer.ProvenanceError, "request_consumed"):
            request.consume("/nonexistent-polaris-unit-socket")

    @unittest.skipIf(importlib.util.find_spec("cryptography"), "project crypto is available")
    def test_project_crypto_missing_explicitly_denies(self) -> None:
        consumer = importlib.import_module(MODULE)
        envelope = {
            "version": 1,
            "issuerId": "unit-issuer",
            "algorithm": "Ed25519",
            "signedBytes": base64.b64encode(b"paperclip.workspace-binary-provenance/v1\0{}").decode("ascii"),
            "signature": base64.b64encode(b"x" * 64).decode("ascii"),
            "payload": {},
        }
        pin = consumer.IssuerPin("unit-issuer", "unit-epoch", b"not-a-key")
        context = consumer.ExpectedContext({}, "a" * 64, "port", "challenge", {}, {}, "statmount-unique-v1")
        with self.assertRaisesRegex(consumer.ProvenanceError, "crypto_unavailable"):
            consumer.verify_envelope(json.dumps(envelope).encode("utf-8"), pin, context)

    def test_duplicate_json_keys_rejected_before_consumption(self) -> None:
        consumer = importlib.import_module(MODULE)
        with self.assertRaisesRegex(consumer.ProvenanceError, "duplicate"):
            consumer.decode_json(b'{"version":1,"version":1}')

    def test_nonfinite_json_rejected(self) -> None:
        consumer = importlib.import_module(MODULE)
        for data in [b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}']:
            with self.assertRaises(consumer.ProvenanceError):
                consumer.decode_json(data)

    def test_real_self_and_current_thread_are_kernel_observations(self) -> None:
        consumer = importlib.import_module(MODULE)
        view = consumer.capture_self_identity()
        self.assertEqual(view["pid"], os.getpid())
        self.assertEqual(view["thread"]["pid"], os.getpid())
        self.assertEqual(view["thread"]["namespaces"], view["namespaces"])
        self.assertGreater(int(view["start"]), 0)
        self.assertEqual(view["bootId"], open_boot_id())

    def test_no_release_policy_in_guest_environment(self) -> None:
        consumer = importlib.import_module(MODULE)
        os.environ["POLARIS_PROVENANCE_ISSUER_PIN"] = "guest-selected"
        try:
            with self.assertRaisesRegex(consumer.ProvenanceError, "release_policy_unavailable"):
                consumer.load_worker_release_policy()
        finally:
            os.environ.pop("POLARIS_PROVENANCE_ISSUER_PIN")


def open_boot_id() -> str:
    with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as stream:
        return stream.read().strip()


@unittest.skipUnless(
    importlib.util.find_spec("cryptography"),
    "crypto absent in project interpreter; use explicit system component probe",
)
class SignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        self.consumer = importlib.import_module(MODULE)
        self.key = Ed25519PrivateKey.generate()  # Temporary unit issuer only.
        self.pin = self.consumer.IssuerPin(
            "unit-temporary-issuer",
            "unit-epoch",
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
        )
        self.nonce = "a" * 64
        namespaces = {}
        for name, label in [("user", "user"), ("pid", "pid"), ("mnt", "mount")]:
            observed = os.stat("/proc/self/ns/" + name)
            namespaces[label] = {"device": str(observed.st_dev), "inode": str(observed.st_ino)}
        with open("/proc/self/stat", encoding="utf-8") as stream:
            process_stat = stream.read()
        start = process_stat[process_stat.rfind(")") + 2 :].split()[19]
        outer = {
            "pid": os.getpid(),
            "start": start,
            "bootId": open_boot_id(),
            "uid": os.getuid(),
            "gid": os.getgid(),
            "userNamespace": namespaces["user"],
            "pidNamespace": namespaces["pid"],
            "mountNamespace": namespaces["mount"],
        }
        fd = os.open("/usr/bin/bwrap", os.O_RDONLY)
        try:
            metadata = os.fstat(fd)
            with open(f"/proc/self/fdinfo/{fd}", encoding="utf-8") as stream:
                mount_id = next(line.split()[1] for line in stream if line.startswith("mnt_id:"))
            with open("/usr/bin/bwrap", "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
        finally:
            os.close(fd)
        expected = {
            "device": str(metadata.st_dev),
            "inode": str(metadata.st_ino),
            "size": str(metadata.st_size),
            "sha256": digest,
        }
        # Host ownership in this signed-schema fixture is synthetic. Neither
        # this issuer nor this fixture claims actual HOST/immutable admission.
        binary = {
            **expected,
            "uid": 0,
            "gid": 0,
            "mode": stat.S_IMODE(metadata.st_mode),
            "nlink": 1,
            "regular": True,
            "mountId": mount_id,
        }
        root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            root_stat = os.fstat(root_fd)
            with open(f"/proc/self/fdinfo/{root_fd}", encoding="utf-8") as stream:
                root_mount = next(line.split()[1] for line in stream if line.startswith("mnt_id:"))
        finally:
            os.close(root_fd)
        observation = {
            "version": 1,
            "nonce": self.nonce,
            "bootId": open_boot_id(),
            "kernelCredentialScope": "kernel-validated-referenced-process",
            "kernelCredentialClaims": {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()},
            "referencedProcess": {"pid": os.getpid(), "start": start},
            "outer": outer,
            "namespaces": namespaces,
            "root": {"device": str(root_stat.st_dev), "inode": str(root_stat.st_ino), "mountId": root_mount},
            "hostBinary": copy.deepcopy(binary),
            "peerBinary": copy.deepcopy(binary),
            "mountProof": {
                "policy": "kernel-mountinfo-idmapped-v1",
                "hostFilesystem": "ext4",
                "peerFilesystem": "ext4",
                "hostIdmapped": False,
                "peerIdmapped": False,
            },
            "ancestry": {"user": True, "pid": True},
        }
        self.now = time.time_ns() // 1_000_000
        self.payload = {
            "version": 1,
            "issuerId": self.pin.issuer_id,
            "purpose": "canonical-bwrap-ownership-facts",
            "canonicalPath": "/usr/bin/bwrap",
            "binding": {
                "ownerId": "unit-owner",
                "companyId": "unit-company",
                "runId": "unit-run",
                "generation": "1",
                "launchId": "unit-launch",
                "agentId": "unit-agent",
                "issueId": "unit-issue",
                "processPid": os.getpid(),
                "processStartedAt": start,
            },
            "nonce": self.nonce,
            "issuedAt": self.now,
            "expiresAt": self.now + 1000,
            "consumerViewPolicy": "immutable-consumer-self-thread-namespace-root-file-comparison",
            "consumerContext": {"bootstrapPortId": "unit-port", "challengeId": "unit-challenge"},
            "observation": observation,
        }
        self.context = self.consumer.ExpectedContext(
            copy.deepcopy(self.payload["binding"]),
            self.nonce,
            "unit-port",
            "unit-challenge",
            copy.deepcopy(outer),
            copy.deepcopy(expected),
            "kernel-mountinfo-idmapped-v1",
        )

    def envelope(self, payload: dict[str, Any] | None = None, *, signed: bytes | None = None) -> bytes:
        payload = self.payload if payload is None else payload
        signed = (
            b"paperclip.workspace-binary-provenance/v1\0" + json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if signed is None
            else signed
        )
        return json.dumps(
            {
                "version": 1,
                "issuerId": self.pin.issuer_id,
                "algorithm": "Ed25519",
                "signedBytes": base64.b64encode(signed).decode("ascii"),
                "signature": base64.b64encode(self.key.sign(signed)).decode("ascii"),
                "payload": payload,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def test_temporary_issuer_roundtrip_is_facts_not_authority(self) -> None:
        result = self.consumer.verify_envelope(self.envelope(), self.pin, self.context, now_ms=self.now)
        self.assertEqual(result.payload["binding"]["runId"], "unit-run")
        result.payload["binding"]["runId"] = "tampered"
        self.assertEqual(result.payload["binding"]["runId"], "unit-run")
        self.assertFalse(hasattr(result, "execution_grant"))

    def test_signature_and_independent_pin_changes_rejected(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        other = Ed25519PrivateKey.generate()
        pin = self.consumer.IssuerPin(
            self.pin.issuer_id,
            "unit-epoch",
            other.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
        )
        with self.assertRaisesRegex(self.consumer.ProvenanceError, "signature_invalid"):
            self.consumer.verify_envelope(self.envelope(), pin, self.context, now_ms=self.now)
        envelope = json.loads(self.envelope())
        envelope["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
        with self.assertRaisesRegex(self.consumer.ProvenanceError, "signature_invalid"):
            self.consumer.verify_envelope(json.dumps(envelope).encode("utf-8"), self.pin, self.context, now_ms=self.now)

    def test_unsigned_payload_cannot_override_signed_bytes(self) -> None:
        envelope = json.loads(self.envelope())
        envelope["payload"]["binding"]["runId"] = "other-run"
        with self.assertRaisesRegex(self.consumer.ProvenanceError, "unsigned_payload_mismatch"):
            self.consumer.verify_envelope(json.dumps(envelope).encode("utf-8"), self.pin, self.context, now_ms=self.now)

    def test_signed_negative_schema_context_lifetime_cases(self) -> None:
        cases = [
            ("nonce", "b" * 64),
            ("canonicalPath", "/tmp/bwrap"),
            ("purpose", "execution-grant"),
            ("issuedAt", True),
            ("expiresAt", self.now),
            ("expiresAt", self.now + 1001),
            ("consumerContext", {"bootstrapPortId": "other", "challengeId": "unit-challenge"}),
            ("consumerViewPolicy", "ack-is-authority"),
            ("version", True),
            ("binding", {**self.payload["binding"], "generation": "2"}),
            ("observation", {**self.payload["observation"], "kernelCredentialScope": "actual-sender"}),
            ("observation", {**self.payload["observation"], "ancestry": {"user": 1, "pid": True}}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                payload = copy.deepcopy(self.payload)
                payload[key] = value
                with self.assertRaises(self.consumer.ProvenanceError):
                    self.consumer.verify_envelope(self.envelope(payload), self.pin, self.context, now_ms=self.now)

    def test_signed_domain_duplicate_keys_and_unknown_fields_rejected(self) -> None:
        for signed in [b"wrong-domain\0{}", b'paperclip.workspace-binary-provenance/v1\0{"version":1,"version":1}']:
            with self.assertRaises(self.consumer.ProvenanceError):
                self.consumer.verify_envelope(self.envelope(signed=signed), self.pin, self.context, now_ms=self.now)
        payload = {**self.payload, "issuerKey": "proof-selected"}
        with self.assertRaisesRegex(self.consumer.ProvenanceError, "payload_schema"):
            self.consumer.verify_envelope(self.envelope(payload), self.pin, self.context, now_ms=self.now)

    def test_signed_referenced_other_process_is_not_self(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["observation"]["referencedProcess"]["pid"] += 1
        payload["observation"]["kernelCredentialClaims"]["pid"] += 1
        facts = self.consumer.verify_envelope(self.envelope(payload), self.pin, self.context, now_ms=self.now)
        with self.assertRaisesRegex(self.consumer.ProvenanceError, "self_identity_mismatch"):
            self.consumer.compare_self_view(facts)

    def test_self_complete_bytes_statx_mountinfo_view_component(self) -> None:
        facts = self.consumer.verify_envelope(self.envelope(), self.pin, self.context, now_ms=self.now)
        observed = self.consumer.compare_self_view(facts)
        self.assertEqual(observed["binary"]["sha256"], self.payload["observation"]["peerBinary"]["sha256"])
        self.assertEqual(observed["self"]["pid"], os.getpid())

    def test_real_seqpacket_kernel_credentials_ack_and_consumed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pol56-component-", dir=os.environ.get("PAPERCLIP_RUN_SCRATCH_DIR")
        ) as directory:
            locator = directory + "/proof.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
                server.settimeout(2)
                server.bind(locator)
                server.listen(1)
                errors: list[Exception] = []
                seen: list[str] = []

                def serve() -> None:
                    try:
                        with server.accept()[0] as connection:
                            connection.settimeout(2)
                            connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                            data, ancillary, flags, _ = connection.recvmsg(1024, socket.CMSG_SPACE(12))
                            self.assertEqual(json.loads(data), {"version": 1, "nonce": self.nonce})
                            self.assertEqual(flags, 0)
                            self.assertEqual(ancillary[0][:2], (socket.SOL_SOCKET, socket.SCM_CREDENTIALS))
                            self.assertEqual(
                                struct.unpack("3i", ancillary[0][2]), (os.getpid(), os.getuid(), os.getgid())
                            )
                            connection.send(self.envelope())
                            ack, ancillary, flags, _ = connection.recvmsg(1024, socket.CMSG_SPACE(12))
                            self.assertEqual(json.loads(ack), {"version": 1, "nonce": self.nonce, "ack": True})
                            self.assertEqual(flags, 0)
                            self.assertEqual(
                                struct.unpack("3i", ancillary[0][2]), (os.getpid(), os.getuid(), os.getgid())
                            )
                            seen.append("ack")
                            connection.send(
                                json.dumps({"version": 1, "nonce": self.nonce, "consumed": True}).encode("ascii")
                            )
                    except (OSError, AssertionError, ValueError) as exc:
                        errors.append(exc)

                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    consumer = self.consumer.BinaryFactsConsumer(self.pin, self.context)
                    facts = consumer.consume(locator)
                    self.assertEqual(facts.payload["nonce"], "a" * 64)
                    with self.assertRaisesRegex(self.consumer.ProvenanceError, "request_consumed"):
                        consumer.consume(locator)
                finally:
                    thread.join(timeout=3)
                self.assertFalse(thread.is_alive())  # Thread completion is test cleanup only.
                self.assertEqual(errors, [])
                self.assertEqual(seen, ["ack"])

    def test_expiry_during_final_real_self_comparison_cannot_return_facts(self) -> None:
        # Inject processing latency only; all Linux comparisons remain real.
        real_compare = self.consumer.compare_self_view

        def slow_compare(facts: Any) -> dict[str, Any]:
            observed = real_compare(facts)
            time.sleep(0.12)
            return dict(observed)

        with tempfile.TemporaryDirectory(
            prefix="pol56-expiry-", dir=os.environ.get("PAPERCLIP_RUN_SCRATCH_DIR")
        ) as directory:
            locator = directory + "/proof.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
                server.settimeout(2)
                server.bind(locator)
                server.listen(1)
                failures: list[Exception] = []

                def serve() -> None:
                    try:
                        with server.accept()[0] as connection:
                            connection.settimeout(2)
                            connection.recv(1024)
                            payload = copy.deepcopy(self.payload)
                            payload["issuedAt"] = time.time_ns() // 1_000_000
                            payload["expiresAt"] = payload["issuedAt"] + 200
                            connection.send(self.envelope(payload))
                            connection.recv(1024)
                            connection.send(
                                json.dumps({"version": 1, "nonce": self.nonce, "consumed": True}).encode("ascii")
                            )
                    except (OSError, AssertionError, ValueError) as exc:
                        failures.append(exc)

                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    with (
                        patch.object(self.consumer, "compare_self_view", slow_compare),
                        self.assertRaisesRegex(self.consumer.ProvenanceError, "proof_expired"),
                    ):
                        self.consumer.BinaryFactsConsumer(self.pin, self.context).consume(locator)
                finally:
                    thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
