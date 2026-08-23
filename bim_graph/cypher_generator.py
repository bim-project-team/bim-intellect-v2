"""Generates Cypher queries from natural language using an LLM.

Safety guardrails block destructive keywords and ensure the query starts
with a read-only clause (MATCH, RETURN, CALL, SHOW, WITH).
"""

import logging
import re

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
        "DROP", "DELETE", "DETACH", "REMOVE", "SET", "CREATE", "MERGE",
        "LOAD CSV", "FOREACH", "apoc.periodic.iterate", "apoc.trigger",
    ]

    def __init__(self, model: str = CHAT_MODEL):
        self.model = model

    def generate(self, question: str) -> str:
        """Convert user question to a safe Cypher query."""
        prompt = CYPHER_GENERATOR_PROMPT.format(question=question)
        return self._generate_from_prompt(prompt, "cypher generation")

    def generate_completion(
        self, question: str, previous_query: str, missing_columns: list[str]
    ) -> str:
        """One bounded correction when a result violates its output contract."""
        prompt = CYPHER_GENERATOR_PROMPT.format(question=question) + (
            "\n\nThe previous read-only query was incomplete:\n"
            f"{previous_query}\n"
            f"It omitted these required RETURN aliases: {missing_columns}.\n"
            "Return one corrected query containing every required alias. Preserve any existing "
            "$parameter names and all filters. Return only Cypher."
        )
        return self._generate_from_prompt(prompt, "cypher completeness repair")

    def _generate_from_prompt(self, prompt: str, op_name: str) -> str:
        messages = [{"role": "user", "content": prompt}]

        try:
            response = call_with_retries(
                _chat_callable(messages, self.model, 0.1),
                op_name=op_name,
            )
            cypher = response.choices[0].message.content or ""
        except (LLMRequestError, LLMConfigError) as exc:
            logger.error("Cypher generation failed: %s", exc)
            raise

        # Strip markdown fences if the model added them
        cypher = cypher.replace("```cypher", "").replace("```", "").strip()

        # Safety: block destructive keywords
        # Ignore string-literal contents when scanning keywords, then reject
        # additional statements and all mutating clauses/procedures.
        safety_text = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", cypher)
        upper_cypher = safety_text.upper()
        if ";" in safety_text.rstrip().rstrip(";"):
            raise ValueError("Generated query contains multiple statements.")
        for keyword in self.FORBIDDEN_KEYWORDS:
            pattern = rf"(?<![A-Z0-9_]){re.escape(keyword.upper())}(?![A-Z0-9_])"
            if re.search(pattern, upper_cypher):
                raise ValueError(
                    f"Generated query contains forbidden keyword '{keyword}'. "
                    f"Query was: {cypher[:200]}..."
                )

        # Safety: must start with a read keyword
        first_word = upper_cypher.split()[0] if cypher else ""
        if first_word not in ("MATCH", "RETURN", "SHOW", "WITH"):
            raise ValueError(
                f"Generated query does not start with a safe read keyword. "
                f"Query was: {cypher[:200]}..."
            )

        # INFO (not DEBUG): the generated query is the single most useful
        # piece of information when a graph question returns unexpected
        # results. Keeping this hidden behind DEBUG turns a one-query
        # diagnosis into a multi-step manual investigation - it should be
        # visible in normal logs, not opt-in.
        logger.info("Generated Cypher: %s", cypher[:300])
        return cypher
