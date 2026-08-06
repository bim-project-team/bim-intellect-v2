"""
openrouter_client.py
---------------------
Shared OpenRouter configuration, client construction, and retry/error
handling used by both the embedding pipeline (embedder.py) and the RAG
chat pipeline (retriever.py).

OpenRouter exposes an OpenAI-compatible API, so the official `openai`
SDK works as a drop-in client pointed at OpenRouter's base URL instead
of api.openai.com - request/response shapes and the SDK's typed
exceptions are unchanged, only base_url, api_key, and model slugs
differ. See: https://openrouter.ai/docs/quickstart#using-the-openai-sdk

Centralizing this here (instead of duplicating client setup + retry
logic in embedder.py and retriever.py) means there is exactly one place
that knows how to talk to OpenRouter and one place that decides what's
retryable.
"""
import logging
import os
import random
import time
from typing import Callable, TypeVar

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("bim_intellect.rag")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model slugs on OpenRouter are "<provider>/<model>". These resolve to the
# same underlying OpenAI models the system used before switching from
# api.openai.com to OpenRouter - override via env var if needed.
CHAT_MODEL = os.getenv("OPENROUTER_CHAT_MODEL", "openai/gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("OPENROUTER_EMBEDDING_MODEL", "openai/text-embedding-3-small")

# Optional attribution headers (OpenRouter leaderboards only - not required
# for the API to function). See https://openrouter.ai/docs/app-attribution
SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
SITE_NAME = os.getenv("OPENROUTER_SITE_NAME", "BIM-Intellect")

MAX_RETRIES = int(os.getenv("OPENROUTER_MAX_RETRIES", "2"))
BASE_BACKOFF_SECONDS = float(os.getenv("OPENROUTER_BASE_BACKOFF", "1.5"))


class LLMConfigError(RuntimeError):
    """Configuration problem (missing/invalid API key). Not retryable -
    the caller needs to fix their setup, not try again."""


class LLMRequestError(RuntimeError):
    """A request to OpenRouter ultimately failed - either a non-retryable
    error, or a retryable one that didn't succeed within MAX_RETRIES."""


_client: OpenAI | None = None


def get_client() -> OpenAI:
    """
    Lazily construct a single shared OpenAI-SDK client pointed at
    OpenRouter. Fails fast with a clear LLMConfigError if the API key is
    missing, instead of letting every downstream call fail with an
    opaque 401.
    """
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMConfigError(
            "OPENROUTER_API_KEY is not set. Create a key at "
            "https://openrouter.ai/keys and add it to your .env file."
        )

    default_headers = {"X-Title": SITE_NAME}
    if SITE_URL:
        default_headers["HTTP-Referer"] = SITE_URL

    _client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers=default_headers,
    )
    return _client


T = TypeVar("T")

# Transient failures worth retrying: rate limits, connection blips, and
# timeouts. A 5xx APIStatusError is also retried (handled separately
# below) since that's OpenRouter/the upstream provider being temporarily
# overloaded, not a problem with the request itself.
_RETRYABLE_EXCEPTIONS = (RateLimitError, APIConnectionError, APITimeoutError)


def call_with_retries(fn: Callable[[], T], *, op_name: str,
                       max_retries: int = MAX_RETRIES) -> T:
    """
    Run `fn` (a zero-arg call into the OpenRouter/OpenAI SDK), retrying
    transient failures with exponential backoff + jitter.

    Every failure path ends in either a successful return or an
    LLMConfigError/LLMRequestError - callers never need to know about
    the underlying `openai` SDK's exception hierarchy, just these two.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except AuthenticationError as exc:
            # A bad key won't fix itself on retry.
            raise LLMConfigError(
                f"{op_name} failed: OpenRouter rejected the API key (401). "
                f"Check OPENROUTER_API_KEY."
            ) from exc
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            _sleep_backoff(op_name, attempt, max_retries, exc)
        except APIStatusError as exc:
            # Only 5xx (provider/OpenRouter overloaded) is worth retrying.
            # 4xx like bad request, model not found, or content policy
            # rejections will fail the same way every time.
            if 500 <= exc.status_code < 600 and attempt < max_retries:
                last_exc = exc
                _sleep_backoff(op_name, attempt, max_retries, exc)
                continue
            raise LLMRequestError(f"{op_name} failed ({exc.status_code}): {exc}") from exc
        except OpenAIError as exc:
            # Catch-all so no raw SDK exception type escapes this module.
            raise LLMRequestError(f"{op_name} failed: {exc}") from exc

    raise LLMRequestError(
        f"{op_name} failed after {max_retries} attempt(s): {last_exc}"
    ) from last_exc


def _sleep_backoff(op_name: str, attempt: int, max_retries: int, exc: Exception) -> None:
    delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
    logger.warning(
        "%s failed (attempt %d/%d): %s - retrying in %.1fs",
        op_name, attempt, max_retries, exc, delay,
    )
    time.sleep(delay)
