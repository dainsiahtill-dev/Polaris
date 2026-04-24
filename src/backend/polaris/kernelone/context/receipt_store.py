"""ReceiptStore - stores large tool outputs via ContentStore v2.1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.content_store import ContentRef

from polaris.kernelone.context.context_os.content_store import ContentStore


class ReceiptStore:
    """Workspace-scoped receipt storage backed by ContentStore.

    Receipts are large tool outputs (search results, diffs, file slices,
    async receipts) that should not be duplicated in prompt text.
    """

    def __init__(self, workspace: str = ".") -> None:
        self.workspace = workspace
        self._content_store = ContentStore(workspace=workspace)
        self._index: dict[str, ContentRef] = {}

    def put(self, receipt_id: str, content: str) -> str:
        """Store receipt content and return its content hash."""
        ref = self._content_store.intern(content)
        self._index[receipt_id] = ref
        return ref.hash

    def get(self, receipt_id: str) -> str | None:
        """Retrieve receipt content by receipt_id."""
        ref = self._index.get(receipt_id)
        if ref is None:
            return None
        return self._content_store.get_if_present(ref)

    def offload_content(
        self,
        receipt_id: str,
        content: str,
        *,
        threshold: int,
        placeholder: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Store oversized content and return display text plus receipt refs."""
        if len(content) <= threshold:
            return content, ()
        self.put(receipt_id, content)
        return placeholder, (receipt_id,)

    def export_receipts(self) -> dict[str, str]:
        """Export all currently indexed receipts as inline content."""
        exported: dict[str, str] = {}
        for receipt_id, ref in self._index.items():
            value = self._content_store.get_if_present(ref)
            if value is not None:
                exported[receipt_id] = value
        return exported

    def import_receipts(self, payload: Mapping[str, Any] | None) -> None:
        """Restore receipt contents from an exported mapping."""
        if not isinstance(payload, Mapping):
            return
        for receipt_id, value in payload.items():
            if isinstance(receipt_id, str) and isinstance(value, str):
                self.put(receipt_id, value)

    def list_receipt_ids(self) -> list[str]:
        """Return all tracked receipt ids."""
        return list(self._index.keys())

    def get_by_batch_idempotency_key(self, batch_idempotency_key: str) -> dict[str, Any] | None:
        """Query receipt by batch idempotency key.

        Phase 1.5: Enables ToolBatch idempotency by checking if a batch
        with the given idempotency key has already been executed.

        Args:
            batch_idempotency_key: The idempotency key (format: turn_id:batch_seq)

        Returns:
            Cached receipt dict if found, None otherwise.
        """
        # Look for receipt_id prefixed with the batch key
        prefix = f"batch:{batch_idempotency_key}:"
        for receipt_id in self._index:
            if receipt_id.startswith(prefix):
                content = self.get(receipt_id)
                if content is not None:
                    import json

                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return {"content": content, "receipt_id": receipt_id}
        return None

    def put_batch_receipt(self, batch_idempotency_key: str, receipt: dict[str, Any]) -> str:
        """Store a batch receipt with idempotency key for later lookup.

        Args:
            batch_idempotency_key: The idempotency key (format: turn_id:batch_seq)
            receipt: The receipt dict to store

        Returns:
            The content hash of the stored receipt.
        """
        import json

        receipt_id = f"batch:{batch_idempotency_key}:receipt"
        content = json.dumps(receipt, ensure_ascii=False)
        return self.put(receipt_id, content)
