"""Backward-compatible regulation-only facade over the v2 orchestrator."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from .memory import ConversationStore
from .orchestrator import RAGOrchestrator


@dataclass
class ChatSession:
    """Legacy API that now uses bounded server-side memory and complete retrieval."""

    n_results: int = 10
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _memory: ConversationStore = field(default_factory=ConversationStore)

    def ask(self, question: str) -> dict:
        return RAGOrchestrator(n_results=self.n_results, memory=self._memory).ask(
            question,
            conversation_id=self.conversation_id,
            source_override="vector",
        )

    def reset(self) -> None:
        self._memory.clear(self.conversation_id)


def answer_question(question: str, n_results: int = 10) -> dict:
    return RAGOrchestrator(n_results=n_results).ask(question, source_override="vector")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ask the regulation RAG pipeline")
    parser.add_argument("question")
    parser.add_argument("--conversation-id")
    parser.add_argument("--strong", action="store_true")
    args = parser.parse_args()
    result = RAGOrchestrator().ask(
        args.question,
        conversation_id=args.conversation_id,
        use_strong_models=args.strong,
        source_override="vector",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
