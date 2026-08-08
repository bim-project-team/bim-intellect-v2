"""
retriever.py
-------------
RAG retrieval + generation with mandatory clause citations, now served
through OpenRouter instead of hitting api.openai.com directly - same
underlying model (gpt-4o-mini), same prompt engineering / no fine-tuning
approach.

Adds a proper multi-turn ChatSession: previous question/answer pairs are
carried forward so follow-up questions ("what about for a hospital?")
resolve correctly, while each turn still re-runs retrieval fresh against
the current question so answers stay grounded in the regulation text
rather than drifting from stale context.
"""
import json
from dataclasses import dataclass, field

from embedder import query_similar
from openrouter_client import (
    CHAT_MODEL,
    LLMConfigError,
    LLMRequestError,
    call_with_retries,
    get_client,
    logger,
)

SYSTEM_PROMPT = """You are a regulatory compliance assistant for building code Mabhas 15
(elevators and escalators). Answer ONLY using the provided context chunks.
Every factual claim MUST cite its source in the format [Clause X.X, Page Y],
using exactly the clause number and page number given in the context block
for that chunk (each context block is already tagged like "[Clause X.X, Page Y]").
If the context does not contain a clear answer, say so explicitly instead of guessing.
Never invent a clause number or page number that is not present in the provided context."""

NO_CONTEXT_ANSWER = (
    "I couldn't find any clauses in the indexed regulation text relevant "
    "to that question. Try rephrasing, or confirm the right document has "
    "been ingested via embedder.py."
)


def build_context(results: dict) -> str:
    """Turn a Chroma query result into a citation-tagged context block.
    Returns "" if nothing was retrieved (empty collection, no matches)."""
    docs = results.get("documents") or [[]]
    metas = results.get("metadatas") or [[]]
    if not docs or not docs[0]:
        return ""
    blocks = []
    for doc, meta in zip(docs[0], metas[0]):
        clause = meta.get("clause_id", "unknown")
        page = meta.get("page_number", "unknown")
        blocks.append(f"[Clause {clause}, Page {page}] {doc}")
    return "\n\n".join(blocks)


@dataclass
class ChatSession:
    """
    A single ongoing conversation. Retrieval runs fresh every turn (each
    question gets its own most-relevant chunks), but the running
    question/answer history is sent along too, so the model can resolve
    references to earlier turns ("and what about...", "why not...").

    max_history_messages caps how many prior messages (question+answer
    pairs, so always an even number) are replayed each turn, to keep
    token usage/cost bounded on long conversations - oldest turns are
    dropped first.
    """
    n_results: int = 5
    max_history_messages: int = 20  # 10 question/answer pairs
    history: list[dict] = field(default_factory=list)

    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            raise LLMRequestError("Question was empty.")

        # --- Retrieval (raises LLMConfigError/LLMRequestError on failure -
        # let it propagate; there's nothing useful to answer without it) ---
        results = query_similar(question, n_results=self.n_results)
        context = build_context(results)

        if not context:
            logger.info("No matching context found for question - skipping the LLM call.")
            answer = NO_CONTEXT_ANSWER
            self._append_turn(question, answer)
            return {"answer": answer, "sources": [], "used_context": False}

        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + self.history[-self.max_history_messages:]
            + [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}]
        )

        def do_chat():
            client = get_client()
            return client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=0.1,
            )

        response = call_with_retries(do_chat, op_name="chat completion")
        answer = response.choices[0].message.content or (
            "The model returned an empty response - try rephrasing the question."
        )

        self._append_turn(question, answer)
        return {
            "answer": answer,
            "sources": results["metadatas"][0],
            "used_context": True,
        }

    def _append_turn(self, question: str, answer: str) -> None:
        # Store the raw question, not the context-augmented prompt - each
        # turn re-retrieves its own context, so replaying old context here
        # would just bloat tokens with stale, possibly superseded chunks.
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})

    def reset(self) -> None:
        self.history.clear()

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            self.history = json.load(f)


def answer_question(question: str, n_results: int = 5) -> dict:
    """Single-turn convenience wrapper (no history kept) - used by the
    non-interactive CLI mode and available for other callers that just
    want a one-off answer."""
    return ChatSession(n_results=n_results).ask(question)


def _run_single_question(question: str) -> None:
    try:
        result = answer_question(question)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except LLMConfigError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1)
    except LLMRequestError as exc:
        print(f"Request failed: {exc}")
        raise SystemExit(1)


def _run_interactive_chat() -> None:
    print("BIM-Intellect regulation chat. Type 'exit' to quit, 'reset' to clear history.\n")
    session = ChatSession()

    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return

        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            print("bye.")
            return
        if question.lower() == "reset":
            session.reset()
            print("(history cleared)\n")
            continue

        try:
            result = session.ask(question)
        except LLMConfigError as exc:
            # Config problems won't resolve themselves mid-session.
            print(f"\nConfiguration error: {exc}")
            return
        except LLMRequestError as exc:
            # Transient/request-level failure - let the user try again
            # without losing the conversation so far.
            print(f"\n[request failed - try again: {exc}]\n")
            continue

        print(f"\nassistant> {result['answer']}")
        if result["sources"]:
            citations = ", ".join(
                f"Clause {s.get('clause_id', 'unknown')} (p.{s.get('page_number', 'unknown')})"
                for s in result["sources"]
            )
            print(f"  sources: {citations}")
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        _run_single_question(sys.argv[1])
    else:
        _run_interactive_chat()