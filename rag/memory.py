"""Bounded, thread-safe in-process conversation memory."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .config import SETTINGS, RAGSettings


@dataclass
class ConversationState:
    conversation_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    topic: str = ""
    used_chunk_ids: set[str] = field(default_factory=set)
    last_needs_vector: bool | None = None
    last_needs_graph: bool | None = None
    last_needs_sustainability: bool | None = None
    last_document_domains: tuple[str, ...] = ()
    sustainability_findings: list[dict[str, Any]] = field(default_factory=list)
    last_access: float = field(default_factory=time.time)

    def context(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "summary": self.summary,
            "topic": self.topic,
            "recent_messages": list(self.messages),
            "used_chunk_ids": sorted(self.used_chunk_ids),
            "last_routing": {
                "vector": self.last_needs_vector,
                "graph": self.last_needs_graph,
                "sustainability": self.last_needs_sustainability,
                "document_domains": list(self.last_document_domains),
            },
            "sustainability_findings": list(self.sustainability_findings),
        }


class ConversationStore:
    def __init__(self, settings: RAGSettings = SETTINGS):
        self.settings = settings
        self._states: OrderedDict[str, ConversationState] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, conversation_id: str | None = None) -> ConversationState:
        with self._lock:
            self._evict()
            conversation_id = conversation_id or str(uuid.uuid4())
            state = self._states.pop(conversation_id, None)
            if state is None:
                state = ConversationState(conversation_id=conversation_id)
            state.last_access = time.time()
            self._states[conversation_id] = state
            self._evict()
            return state

    def append_turn(
        self,
        state: ConversationState,
        question: str,
        answer: str,
        topic: str | None = None,
        chunk_ids: list[str] | None = None,
        needs_vector: bool | None = None,
        needs_graph: bool | None = None,
        needs_sustainability: bool | None = None,
        document_domains: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        with self._lock:
            state.messages.extend([
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ])
            state.topic = topic or state.topic
            state.used_chunk_ids.update(chunk_ids or [])
            if needs_vector is not None:
                state.last_needs_vector = needs_vector
            if needs_graph is not None:
                state.last_needs_graph = needs_graph
            if needs_sustainability is not None:
                state.last_needs_sustainability = needs_sustainability
            if document_domains is not None:
                state.last_document_domains = tuple(document_domains)
            overflow = len(state.messages) - self.settings.memory_recent_messages
            if overflow > 0:
                older = state.messages[:overflow]
                state.messages = state.messages[overflow:]
                compact = "\n".join(
                    f"{message['role']}: {message['content']}" for message in older
                )
                state.summary = (state.summary + "\n" + compact).strip()[-self.settings.memory_summary_chars:]
            state.last_access = time.time()

    def clear(self, conversation_id: str) -> bool:
        with self._lock:
            return self._states.pop(conversation_id, None) is not None

    def record_sustainability_finding(
        self,
        state: ConversationState,
        finding: dict[str, Any],
        *,
        limit: int = 20,
    ) -> None:
        """Retain a bounded session ledger of already-grounded assessments.

        These records are presentation/report snapshots, not deterministic
        carbon inputs and not persisted LEED credit decisions.
        """
        with self._lock:
            key = (
                str(finding.get("criterion", "")).casefold(),
                str(finding.get("project_id", "")),
                tuple(sorted(finding.get("file_ids") or [])),
            )
            state.sustainability_findings = [
                item for item in state.sustainability_findings
                if (
                    str(item.get("criterion", "")).casefold(),
                    str(item.get("project_id", "")),
                    tuple(sorted(item.get("file_ids") or [])),
                ) != key
            ]
            state.sustainability_findings.append(dict(finding))
            state.sustainability_findings = state.sustainability_findings[-max(1, limit):]
            state.last_access = time.time()

    def get_sustainability_findings(
        self,
        conversation_id: str | None,
        *,
        project_id: str,
        file_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not conversation_id:
            return []
        with self._lock:
            self._evict()
            state = self._states.get(conversation_id)
            if state is None:
                return []
            scope = tuple(sorted(file_ids))
            state.last_access = time.time()
            return [
                dict(item) for item in state.sustainability_findings
                if item.get("project_id") == project_id
                and tuple(sorted(item.get("file_ids") or [])) == scope
            ]

    def _evict(self) -> None:
        cutoff = time.time() - self.settings.memory_ttl_seconds
        expired = [key for key, value in self._states.items() if value.last_access < cutoff]
        for key in expired:
            self._states.pop(key, None)
        while len(self._states) > self.settings.memory_max_conversations:
            self._states.popitem(last=False)


conversation_store = ConversationStore()
