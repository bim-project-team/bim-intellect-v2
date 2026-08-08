"""Generates Cypher queries from natural language using an LLM.

Safety guardrails block destructive keywords and ensure the query starts
with a read-only clause (MATCH, RETURN, CALL, SHOW, WITH).
"""

import logging

from rag.openrouter_client import (
    CHAT_MODEL,
    LLMConfigError,
    LLMRequestError,
    call_with_retries,
    get_client,
)

from rag.prompts import CYPHER_GENERATOR_PROMPT

logger = logging.getLogger("bim_intellect.cypher")


def _chat_callable(messages, model, temperature):
    """Zero-arg callable factory for call_with_retries."""
    def _call():
        client = get_client()
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
    return _call


class CypherGenerator:
    """Converts natural language questions into read-only Cypher queries."""

    # Destructive keywords to block for safety
    FORBIDDEN_KEYWORDS = [
        "DROP", "DELETE", "REMOVE", "SET", "CREATE", "MERGE",
        "apoc.periodic.iterate", "apoc.trigger",
    ]

    def __init__(self, model: str = CHAT_MODEL):
        self.model = model

    def generate(self, question: str) -> str:
        """Convert user question to a safe Cypher query."""
        prompt = CYPHER_GENERATOR_PROMPT.format(question=question)
        messages = [{"role": "user", "content": prompt}]

        try:
            response = call_with_retries(
                _chat_callable(messages, self.model, 0.1),
                op_name="cypher generation"
            )
            cypher = response.choices[0].message.content or ""
        except (LLMRequestError, LLMConfigError) as exc:
            logger.error("Cypher generation failed: %s", exc)
            raise

        # Strip markdown fences if the model added them
        cypher = cypher.replace("```cypher", "").replace("```", "").strip()

        # Safety: block destructive keywords
        upper_cypher = cypher.upper()
        for keyword in self.FORBIDDEN_KEYWORDS:
            if keyword.upper() in upper_cypher:
                raise ValueError(
                    f"Generated query contains forbidden keyword '{keyword}'. "
                    f"Query was: {cypher[:200]}..."
                )

        # Safety: must start with a read keyword
        first_word = upper_cypher.split()[0] if cypher else ""
        if first_word not in ("MATCH", "RETURN", "CALL", "SHOW", "WITH"):
            raise ValueError(
                f"Generated query does not start with a safe read keyword. "
                f"Query was: {cypher[:200]}..."
            )

        logger.debug("Generated Cypher: %s", cypher[:200])
        return cypher
