"""State models for the Polaris CLI Director console."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return datetime.now(tz=UTC)
    if isinstance(value, str):
        token = value.strip()
        if token:
            try:
                parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                pass
    return datetime.now(tz=UTC)


def _normalize_artifact_kind(kind: str, language: str) -> str:
    token = _safe_text(kind or language).lower()
    if token in {"md", "markdown"}:
        return "markdown"
    if token == "diff":
        return "diff"
    if token in {"code", "snippet"}:
        return "code"
    return "text"


def _artifact_payloads(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_artifacts = payload.get("artifacts")
    if isinstance(raw_artifacts, Sequence) and not isinstance(raw_artifacts, (str, bytes, bytearray)):
        return [item for item in raw_artifacts if isinstance(item, Mapping)]

    meta = _coerce_mapping(payload.get("meta"))
    nested = meta.get("artifacts")
    if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
        return [item for item in nested if isinstance(item, Mapping)]

    return []


@dataclass(slots=True)
class MessageArtifact:
    artifact_id: str
    kind: str
    title: str
    content: str
    language: str | None = None
    path: str | None = None
    source_message_id: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> MessageArtifact:
        data = _coerce_mapping(payload)
        kind = _normalize_artifact_kind(
            _safe_text(data.get("kind") or data.get("type")), _safe_text(data.get("language"))
        )
        return cls(
            artifact_id=_safe_text(data.get("artifact_id") or data.get("id") or data.get("key")) or "artifact",
            kind=kind,
            title=_safe_text(data.get("title")) or "Artifact",
            content=_safe_text(data.get("content") or data.get("body") or data.get("text")),
            language=_safe_text(data.get("language")) or None,
            path=_safe_text(data.get("path")) or None,
            source_message_id=_safe_text(data.get("source_message_id")) or None,
            summary=_safe_text(data.get("summary")) or None,
            metadata=_coerce_mapping(data.get("metadata")),
            created_at=_parse_timestamp(data.get("created_at") or data.get("timestamp")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "language": self.language,
            "path": self.path,
            "source_message_id": self.source_message_id,
            "summary": self.summary,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class ConsoleMessage:
    id: str
    role: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    artifacts: list[MessageArtifact] = field(default_factory=list)
    thinking: str = ""
    status: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> ConsoleMessage:
        data = _coerce_mapping(payload)
        artifacts = [MessageArtifact.from_mapping(item) for item in _artifact_payloads(data)]
        # Generate unique ID if none provided (prevents all messages sharing "message" ID)
        raw_id = _safe_text(data.get("id") or data.get("message_id") or data.get("uuid"))
        msg_id = raw_id or ("msg-" + str(id(data)) + "-" + str(datetime.now(tz=UTC).timestamp()))
        return cls(
            id=msg_id,
            role=_safe_text(data.get("role")) or "system",
            content=_safe_text(data.get("content") or data.get("text")),
            timestamp=_parse_timestamp(data.get("timestamp") or data.get("created_at") or data.get("updated_at")),
            artifacts=artifacts,
            thinking=_safe_text(data.get("thinking")),
            status=_safe_text(data.get("status")),
            meta=_coerce_mapping(data.get("meta")),
        )

    def with_content(self, content: str) -> ConsoleMessage:
        return replace(self, content=_safe_text(content))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "thinking": self.thinking,
            "status": self.status,
            "meta": dict(self.meta),
        }


@dataclass(slots=True)
class ConsoleSession:
    session_id: str
    title: str
    messages: list[ConsoleMessage] = field(default_factory=list)
    model_parameters: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> ConsoleSession:
        data = _coerce_mapping(payload)
        message_items = data.get("messages")
        messages = (
            [ConsoleMessage.from_mapping(item) for item in message_items if isinstance(item, Mapping)]
            if isinstance(message_items, Sequence) and not isinstance(message_items, (str, bytes, bytearray))
            else []
        )
        return cls(
            session_id=_safe_text(data.get("session_id") or data.get("id")) or "session",
            title=_safe_text(data.get("title")) or "Untitled Session",
            messages=messages,
            model_parameters=_coerce_mapping(data.get("model_parameters") or data.get("parameters")),
            created_at=_parse_timestamp(data.get("created_at")),
            updated_at=_parse_timestamp(data.get("updated_at")),
            metadata=_coerce_mapping(data.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "messages": [message.to_dict() for message in self.messages],
            "model_parameters": dict(self.model_parameters),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class StreamingState:
    active: bool = False
    phase: str = "idle"
    message_id: str | None = None
    buffer: str = ""
    thinking_buffer: str = ""
    last_chunk_at: datetime | None = None
    error: str = ""
    tool_name: str = ""
    auto_scroll: bool = True
    artifact_mode: bool = False

    @classmethod
    def idle(cls) -> StreamingState:
        return cls()

    def mark_chunk(self, *, content: str = "", thinking: str = "", phase: str | None = None) -> StreamingState:
        updated = replace(self)
        if content:
            updated.buffer = f"{updated.buffer}{content}"
        if thinking:
            updated.thinking_buffer = f"{updated.thinking_buffer}{thinking}"
        updated.phase = phase or updated.phase
        updated.last_chunk_at = datetime.now(tz=UTC)
        updated.active = True
        return updated

    def finish(self, *, error: str = "") -> StreamingState:
        updated = replace(self)
        updated.active = False
        updated.phase = "error" if error else "idle"
        updated.error = _safe_text(error)
        updated.last_chunk_at = datetime.now(tz=UTC)
        return updated


@dataclass(slots=True)
class ArtifactPanelState:
    visible: bool = False
    active_tab: str = "markdown"
    title: str = "Artifacts"
    markdown: str = ""
    code: str = ""
    diff: str = ""
    code_language: str | None = None
    source_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_markdown(self, content: str, *, title: str | None = None) -> ArtifactPanelState:
        return replace(
            self,
            visible=True,
            active_tab="markdown",
            title=title or self.title,
            markdown=_safe_text(content),
        )

    def with_code(self, content: str, *, language: str = "python", title: str | None = None) -> ArtifactPanelState:
        return replace(
            self,
            visible=True,
            active_tab="code",
            title=title or self.title,
            code=_safe_text(content),
            code_language=_safe_text(language) or None,
        )

    def with_diff(self, content: str, *, title: str | None = None) -> ArtifactPanelState:
        return replace(
            self,
            visible=True,
            active_tab="diff",
            title=title or self.title,
            diff=_safe_text(content),
        )

    def hide(self) -> ArtifactPanelState:
        return replace(self, visible=False)


def message_from_dict(payload: Mapping[str, Any] | None) -> ConsoleMessage:
    return ConsoleMessage.from_mapping(payload)


def messages_from_dicts(payloads: Sequence[Mapping[str, Any] | None]) -> list[ConsoleMessage]:
    return [ConsoleMessage.from_mapping(item) for item in payloads if isinstance(item, Mapping)]


def session_from_dict(payload: Mapping[str, Any] | None) -> ConsoleSession:
    return ConsoleSession.from_mapping(payload)


def session_from_view(
    *,
    session: Mapping[str, Any] | None,
    messages: Sequence[Mapping[str, Any] | None] | None = None,
) -> ConsoleSession:
    payload = _coerce_mapping(session)
    if messages is not None:
        payload["messages"] = [item for item in messages if isinstance(item, Mapping)]
    return ConsoleSession.from_mapping(payload)


def artifact_from_dict(payload: Mapping[str, Any] | None) -> MessageArtifact:
    return MessageArtifact.from_mapping(payload)


def streaming_state_from_message(
    message: Mapping[str, Any] | None,
    *,
    message_id: str | None = None,
) -> StreamingState:
    data = _coerce_mapping(message)
    return StreamingState(
        active=bool(data.get("active", True)),
        phase=_safe_text(data.get("phase")) or "streaming",
        message_id=_safe_text(message_id or data.get("message_id") or data.get("id")) or None,
        buffer=_safe_text(data.get("content")),
        thinking_buffer=_safe_text(data.get("thinking")),
        error=_safe_text(data.get("error")),
        tool_name=_safe_text(data.get("tool_name")),
        auto_scroll=bool(data.get("auto_scroll", True)),
        artifact_mode=bool(data.get("artifact_mode", False)),
    )


def build_artifact_panel_state(
    artifacts: Sequence[MessageArtifact],
    *,
    active_index: int = 0,
) -> ArtifactPanelState:
    """Factory: build ArtifactPanelState from a sequence of MessageArtifacts.

    Selects the artifact at *active_index* and maps its kind to the
    appropriate panel tab (markdown / code / diff).

    Returns an empty hidden state when *artifacts* is empty.
    """
    if not artifacts:
        return ArtifactPanelState()

    idx = max(0, min(active_index, len(artifacts) - 1))
    artifact = artifacts[idx]
    title = artifact.title or f"Artifact {idx + 1}"

    if artifact.kind == "markdown":
        return ArtifactPanelState().with_markdown(artifact.content, title=title)
    if artifact.kind == "code":
        return ArtifactPanelState().with_code(
            artifact.content,
            language=artifact.language or "python",
            title=title,
        )
    if artifact.kind == "diff":
        return ArtifactPanelState().with_diff(artifact.content, title=title)
    return ArtifactPanelState().with_markdown(artifact.content, title=title)
