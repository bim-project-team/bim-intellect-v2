"""
embedder.py
------------
Embeds text chunks and stores them in a local Chroma vector DB.

Embeddings are generated via OpenRouter (https://openrouter.ai) using the
OpenAI SDK pointed at OpenRouter's endpoint - same underlying model
(text-embedding-3-small) as before, just routed through OpenRouter
instead of hitting api.openai.com directly. See openrouter_client.py for
the shared client/retry setup.
"""
import os

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chunker import Chunk, chunk_pdf
from openrouter_client import (
    EMBEDDING_MODEL,
    LLMConfigError,
    LLMRequestError,
    call_with_retries,
    get_client,
    logger,
)

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
COLLECTION_NAME = "regulations"

# OpenRouter's embeddings endpoint accepts arrays of input strings per
# request; batching keeps individual requests well within provider limits
# and means one slow/failed batch doesn't require re-embedding everything.
EMBED_BATCH_SIZE = int(os.getenv("OPENROUTER_EMBED_BATCH_SIZE", "96"))


class OpenRouterEmbeddingFunction(EmbeddingFunction):
    """
    Chroma-compatible embedding function backed by OpenRouter.

    Chroma calls this automatically whenever documents are added to or a
    query is run against a collection created with this as its
    embedding_function - callers never call it directly.
    """

    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []

        embeddings: Embeddings = []
        total_batches = (len(input) + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE

        for batch_num, start in enumerate(range(0, len(input), EMBED_BATCH_SIZE), start=1):
            batch = input[start:start + EMBED_BATCH_SIZE]
            logger.info("Embedding batch %d/%d (%d chunk(s))...", batch_num, total_batches, len(batch))

            def do_embed(batch=batch):
                client = get_client()
                return client.embeddings.create(model=EMBEDDING_MODEL, input=batch)

            # LLMConfigError / LLMRequestError propagate straight up - a
            # failed batch here means the caller's embed_and_store() call
            # fails loudly rather than silently storing a short vector set.
            response = call_with_retries(do_embed, op_name=f"embedding batch {batch_num}/{total_batches}")
            embeddings.extend(item.embedding for item in response.data)

        return embeddings


def get_client_db() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=CHROMA_DIR)


def get_or_create_collection(client: chromadb.ClientAPI):
    return client.get_or_create_collection(
        name=COLLECTION_NAME, embedding_function=OpenRouterEmbeddingFunction()
    )


def embed_and_store(chunks: list[Chunk]) -> int:
    """
    Embed `chunks` and upsert them into the Chroma collection. Raises
    LLMConfigError (bad/missing API key) or LLMRequestError (embedding
    call ultimately failed) - callers should catch these rather than
    letting a chunked-off traceback reach the end user.
    """
    if not chunks:
        logger.info("No chunks to embed - nothing to do.")
        return 0

    client = get_client_db()
    collection = get_or_create_collection(client)

    logger.info("Storing %d chunk(s) in Chroma collection '%s'...", len(chunks), COLLECTION_NAME)
    try:
        collection.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[{
                "page_number": c.page_number,
                "clause_id": c.clause_id or "unknown",
            } for c in chunks],
        )
    except (LLMConfigError, LLMRequestError):
        raise
    except Exception as exc:
        # Chroma itself can raise (duplicate ids, disk/permission issues,
        # corrupted collection, etc.) - wrap so callers have one error
        # surface to handle instead of guessing at Chroma's exception types.
        raise LLMRequestError(f"Failed to store embeddings in Chroma: {exc}") from exc

    logger.info("Stored %d chunk(s).", len(chunks))
    return len(chunks)


def query_similar(query_text: str, n_results: int = 5):
    """
    Retrieve the n_results chunks most similar to query_text. Raises
    LLMConfigError / LLMRequestError on embedding failure, or
    LLMRequestError if Chroma's own query call fails (e.g. empty/missing
    collection).
    """
    if not query_text or not query_text.strip():
        raise LLMRequestError("query_similar() called with an empty query.")

    client = get_client_db()
    collection = get_or_create_collection(client)

    try:
        if collection.count() == 0:
            logger.warning(
                "Collection '%s' is empty - has embed_and_store() been run yet?",
                COLLECTION_NAME,
            )
        return collection.query(query_texts=[query_text], n_results=n_results)
    except (LLMConfigError, LLMRequestError):
        raise
    except Exception as exc:
        raise LLMRequestError(f"Chroma query failed: {exc}") from exc


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python embedder.py <path-to-pdf>", file=sys.stderr)
        sys.exit(1)

    try:
        chunks = chunk_pdf(sys.argv[1])
        count = embed_and_store(chunks)
        print(f"Embedded and stored {count} chunk(s) in Chroma at {CHROMA_DIR}")
    except LLMConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    except LLMRequestError as exc:
        print(f"Embedding failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"PDF not found: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()