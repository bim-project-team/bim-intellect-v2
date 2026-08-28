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
            },
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

    def _evict(self) -> None:
        cutoff = time.time() - self.settings.memory_ttl_seconds
        expired = [key for key, value in self._states.items() if value.last_access < cutoff]
        for key in expired:
            self._states.pop(key, None)
        while len(self._states) > self.settings.memory_max_conversations:
            self._states.popitem(last=False)


conversation_store = ConversationStore()
