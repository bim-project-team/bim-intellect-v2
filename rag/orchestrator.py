"""Conversation-aware orchestration across regulation and BIM graph knowledge."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from bim_graph.graph_retriever import GraphRetriever

from .config import SETTINGS, ModelProfile, RAGSettings, get_model_profile
from .memory import ConversationState, ConversationStore, conversation_store
from .openrouter_client import LLMConfigError, LLMRequestError, call_with_retries, get_client
from .prompts import (
    CITATION_REPAIR_PROMPT,
    COMBINE_PROMPT,
    CONVERSATION_PROMPT,
    QUERY_UNDERSTANDING_PROMPT,
    ROUTER_PROMPT,
)
from .retrieval import RegulationRetrieval, retrieve_regulations

logger = logging.getLogger("bim_intellect.rag.orchestrator")

_VECTOR_REQUEST_PATTERNS = (
    r"\bpdfs?\b", r"\buploaded\s+(?:document|file|pdf)\b",
    r"(?:پی[‌\- ]?دی[‌\- ]?اف|سند\s+(?:بارگذاری|آپلود)\s*شده|فایل\s+(?:بارگذاری|آپلود)\s*شده)",
)

_GRAPH_REQUEST_PATTERNS = (
    r"\bIfc[A-Za-z][A-Za-z0-9_]*\b", r"\bNeo4j\b", r"\bCLASHES_WITH\b",
    r"\b(?:CLASH|CLEARANCE_VIOLATION|anomalyScore|isAnomaly)\b",
    r"(?:گره|نود|رابطه).{0,40}(?:نوع|تعداد|ویژگی|پراپرتی)",
)
_REGULATION_INTENT_PATTERN = re.compile(
    r"(?:مقررات|ضوابط|استاندارد|الزام|مطابق|سند|منبع|regulation|code|compliance)",
    re.IGNORECASE,
)


def explicitly_requests_vector_context(question: str) -> bool:
    return any(re.search(pattern, question, flags=re.IGNORECASE) for pattern in _VECTOR_REQUEST_PATTERNS)


def explicitly_requests_graph_context(question: str) -> bool:
    return any(re.search(pattern, question, flags=re.IGNORECASE) for pattern in _GRAPH_REQUEST_PATTERNS)


def apply_routing_policy(question: str, decision: dict) -> dict:
    """Conservative guard around semantic LLM routing; never broadens graph access."""
    routed = dict(decision)
    routed.setdefault("needs_vector", True)
    routed.setdefault("needs_graph", True)
    routed.setdefault("is_technical", False)
    routed.setdefault("confidence", None)
    routed.setdefault("reasoning", "No routing explanation was provided.")
    for key in ("needs_vector", "needs_graph", "is_technical"):
        if not isinstance(routed[key], bool):
            raise ValueError(f"Router field {key!r} must be a boolean.")
    if explicitly_requests_graph_context(question):
        routed["needs_graph"] = True
        routed["is_technical"] = True
        if not _REGULATION_INTENT_PATTERN.search(question):
            routed["needs_vector"] = False
        routed["reasoning"] = "Neo4j retrieval required by explicit IFC/schema/property vocabulary."
    if explicitly_requests_vector_context(question) and not routed["needs_vector"]:
        routed["needs_vector"] = True
        routed["reasoning"] = "Vector retrieval required because the question explicitly refers to PDF knowledge."
    elif routed["is_technical"] and not routed["needs_vector"] and not routed["needs_graph"]:
        routed["needs_vector"] = True
        routed["reasoning"] = "Technical question assigned to neither source; conservatively searching the regulation corpus."
    return routed


@dataclass
class RetrievalResult:
    vector_context: str = ""
    graph_context: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    regulation: RegulationRetrieval | None = None
    graph_debug: dict[str, Any] = field(default_factory=dict)

    @property
    def has_any_context(self) -> bool:
        return bool(self.vector_context or self.graph_context)


def valid_source(source: dict) -> bool:
    clause = str(source.get("clause_id", "")).strip().lower()
    return clause not in {"", "unknown", "none", "null"} and source.get("page_number") is not None


def deduplicate_sources(sources: list[dict]) -> list[dict]:
    result, seen = [], set()
    for source in sources:
        if source.get("type", "regulation") == "regulation":
            key = ("regulation", source.get("source"), source.get("clause_id"), source.get("page_number"))
        else:
            key = (source.get("type"), source.get("element_id") or source.get("id"), source.get("name"))
        if key not in seen:
            seen.add(key)
            result.append(source)
    return result


def filter_sources(sources: list[dict]) -> list[dict]:
    return deduplicate_sources([
        source for source in sources
        if source.get("type", "regulation") != "regulation" or valid_source(source)
    ])


_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DASH_PATTERN = r"[-‐‑‒–—−]"
_DIGITS = r"[0-9۰-۹٠-٩]"
# Unlabelled citations are accepted only inside brackets/parentheses. This
# covers localized output without mistaking arbitrary engineering numbers for citations.
CITATION_PATTERN = re.compile(
    rf"(?:"
    rf"[\[(]\s*(?:(?:Clause|بند)\s*)?"
    rf"(?P<bracket_clause>{_DIGITS}+(?:\s*{_DASH_PATTERN}\s*{_DIGITS}+)+)"
    rf"\s*[,،;؛]\s*(?:Page|p\.?|صفحه)\s*(?P<bracket_page>{_DIGITS}+)\s*[\])]"
    rf"|"
    rf"(?:Clause|بند)\s+"
    rf"(?P<label_clause>{_DIGITS}+(?:\s*{_DASH_PATTERN}\s*{_DIGITS}+)+)"
    rf"\s*[,،;؛]?\s*(?:Page|p\.?|صفحه)\s*(?P<label_page>{_DIGITS}+)"
    rf")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Citation:
    clause: str
    page: int
    document: str | None = None


def _normalize_digits(value: Any) -> str:
    return str(value).translate(_DIGIT_TRANSLATION)


def _normalize_clause(value: Any) -> str:
    normalized = _normalize_digits(value).strip()
    return re.sub(rf"\s*{_DASH_PATTERN}\s*", "-", normalized)


def extract_regulation_citations(answer: str) -> list[Citation]:
    citations: list[Citation] = []
    for match in CITATION_PATTERN.finditer(answer or ""):
        clause = match.group("bracket_clause") or match.group("label_clause")
        page = match.group("bracket_page") or match.group("label_page")
        citations.append(Citation(_normalize_clause(clause), int(_normalize_digits(page))))
    return citations


def canonicalize_generated_citations(answer: str) -> str:
    """Render recognized equivalent citations in the public canonical syntax."""
    def replace(match: re.Match) -> str:
        clause = match.group("bracket_clause") or match.group("label_clause")
        page = match.group("bracket_page") or match.group("label_page")
        return f"[Clause {_normalize_clause(clause)}, Page {int(_normalize_digits(page))}]"

    return CITATION_PATTERN.sub(replace, answer or "")


def citation_like_fragments(answer: str) -> list[str]:
    """Small safe diagnostics only; never logs the answer or retrieved document text."""
    candidates = re.findall(
        r"[\[(][^\]\)\r\n]{0,100}(?:Clause|Page|بند|صفحه|p\.)[^\]\)\r\n]{0,100}[\])]",
        answer or "", flags=re.IGNORECASE,
    )
    return candidates[:20]


def cited_regulation_pairs(answer: str) -> set[tuple[str, str]]:
    return {(citation.clause, str(citation.page)) for citation in extract_regulation_citations(answer)}


def supported_regulation_pairs(sources: list[dict]) -> set[tuple[str, str]]:
    return {
        (_normalize_clause(source.get("clause_id", "")), _normalize_digits(source.get("page_number", "")).strip())
        for source in sources if source.get("type", "regulation") == "regulation" and valid_source(source)
    }


def validate_generated_answer(answer: str, sources: list[dict], requires_regulatory_citation: bool = False) -> bool:
    cited, supported = cited_regulation_pairs(answer), supported_regulation_pairs(sources)
    invalid = cited - supported
    if invalid:
        logger.warning(
            "Citation validation failed: generated=%s allowed=%s invalid=%s reason=not_in_current_retrieved_sources",
            sorted(cited), sorted(supported), sorted(invalid),
        )
        return False
    if requires_regulatory_citation and not cited:
        logger.warning(
            "Citation validation failed: generated=[] allowed=%s reason=no_recognizable_regulatory_citation",
            sorted(supported),
        )
        return False
    logger.info("Citation validation result=valid generated=%s allowed=%s", sorted(cited), sorted(supported))
    return True


def sources_used_by_answer(answer: str, sources: list[dict]) -> list[dict]:
    cited = cited_regulation_pairs(answer)
    return [
        source for source in sources
        if source.get("type", "regulation") != "regulation"
        or (_normalize_clause(source.get("clause_id", "")), _normalize_digits(source.get("page_number", "")).strip()) in cited
    ]


def _chat_callable(messages: list[dict], model: str, temperature: float = 0.0):
    def _call():
        return get_client().chat.completions.create(model=model, messages=messages, temperature=temperature)
    return _call


def _json_object(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in model response")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("Model response is not a JSON object")
    return value


def _history_text(state: ConversationState) -> str:
    recent = "\n".join(f"{item['role']}: {item['content']}" for item in state.messages)
    return (
        f"Conversation topic: {state.topic or '(none)'}\n"
        f"Older summary: {state.summary or '(none)'}\n"
        f"Recent turns:\n{recent or '(none)'}"
    )


def _safe_understanding_fallback(question: str, state: ConversationState, error: Exception) -> dict:
    """Last-resort behavior when the semantic router itself is unavailable."""
    normalized = re.sub(r"[!؟?،,.\s]+", " ", question.casefold()).strip()
    social = {
        "سلام", "درود", "ممنون", "متشکرم", "سپاس", "خداحافظ",
        "خیلی خوب بود", "فهمیدم", "باشه", "thanks", "thank you", "hello", "hi",
    }
    if normalized in social:
        return {
            "standalone_query": question, "retrieval_queries": [],
            "needs_vector": False, "needs_graph": False, "is_technical": False,
            "intent": "conversation", "is_follow_up": False,
            "completeness_requested": False, "topic": state.topic,
            "confidence": 0.0, "reasoning": f"social fallback after router error: {error}",
        }
    standalone = f"{state.topic}: {question}" if state.topic else question
    fallback_completeness = bool(state.topic) and (
        len(question.strip()) <= 40
        or bool(re.search(r"(?:همه|تمام|کامل|ادامه|بیشتر|بازم|more|complete|continue)", normalized))
    )
    if state.topic and state.last_needs_vector is not None:
        needs_vector = state.last_needs_vector
        needs_graph = bool(state.last_needs_graph)
    else:
        graph_signal = explicitly_requests_graph_context(question) or bool(re.search(
            r"(?:\bclash\b|\bclearance\b|کلش|تداخل|مدل\s*(?:ساختمان|بیم)?|عنصر\s*(?:شماره)?|این\s+(?:لوله|دیوار|تیر|عنصر))",
            normalized,
        ))
        compliance_signal = bool(re.search(
            r"(?:مقررات|ضوابط|استاندارد|مطابق|مجاز|الزام|compliance|regulation|code)",
            normalized,
        ))
        needs_graph = graph_signal
        needs_vector = compliance_signal or not graph_signal
    return {
        "standalone_query": standalone,
        "retrieval_queries": [standalone],
        "needs_vector": needs_vector,
        "needs_graph": needs_graph,
        "is_technical": True,
        "intent": "technical",
        "is_follow_up": bool(state.topic),
        "completeness_requested": fallback_completeness,
        "topic": state.topic or question[:160],
        "confidence": 0.0,
        "reasoning": f"safe fallback after router error: {error}",
    }


class RAGOrchestrator:
    def __init__(
        self,
        n_results: int | None = None,
        *,
        settings: RAGSettings = SETTINGS,
        memory: ConversationStore = conversation_store,
        graph_retriever: GraphRetriever | None = None,
    ):
        self.settings = settings
        self.memory = memory
        self.graph_retriever = graph_retriever or GraphRetriever()
        self.n_results = n_results or settings.rerank_count

    def route(self, question: str, use_strong_models: bool = False) -> dict:
        """Backward-compatible standalone router used by diagnostics/tests."""
        profile = get_model_profile(use_strong_models, getattr(self, "settings", SETTINGS))
        try:
            response = call_with_retries(
                _chat_callable([
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": question},
                ], profile.router_model),
                op_name="query router",
            )
            return apply_routing_policy(question, _json_object(response.choices[0].message.content))
        except (ValueError, json.JSONDecodeError, LLMConfigError, LLMRequestError) as exc:
            logger.warning("Standalone router failed (%s); searching both technical sources", exc)
            return {
                "needs_vector": True, "needs_graph": True, "is_technical": True,
                "confidence": 0.0, "reasoning": f"router fallback: {exc}",
            }

    def understand(self, question: str, state: ConversationState, profile: ModelProfile) -> dict:
        user_prompt = f"{_history_text(state)}\n\nCurrent message:\n{question}"
        try:
            response = call_with_retries(
                _chat_callable([
                    {"role": "system", "content": QUERY_UNDERSTANDING_PROMPT},
                    {"role": "user", "content": user_prompt},
                ], profile.router_model),
                op_name="contextual query understanding",
            )
            decision = _json_object(response.choices[0].message.content)
            decision = apply_routing_policy(question, decision)
        except (ValueError, json.JSONDecodeError, LLMConfigError, LLMRequestError) as exc:
            logger.warning("Contextual understanding failed (%s); applying bounded safe fallback", exc)
            decision = _safe_understanding_fallback(question, state, exc)
        decision.setdefault("standalone_query", question)
        decision.setdefault("retrieval_queries", [decision["standalone_query"]])
        decision.setdefault("intent", "technical" if decision.get("is_technical") else "conversation")
        decision.setdefault("is_follow_up", False)
        decision.setdefault("completeness_requested", False)
        decision.setdefault("topic", state.topic or decision["standalone_query"][:160])
        if not isinstance(decision["retrieval_queries"], list):
            decision["retrieval_queries"] = [decision["standalone_query"]]
        return decision

    def retrieve(self, understanding: dict) -> RetrievalResult:
        result = RetrievalResult()
        standalone = understanding["standalone_query"]
        if understanding.get("needs_vector"):
            result.regulation = retrieve_regulations(
                standalone,
                understanding.get("retrieval_queries", []),
                completeness_requested=bool(understanding.get("completeness_requested")),
                settings=self.settings,
            )
            result.vector_context = result.regulation.context
            result.sources.extend(result.regulation.sources)
        if understanding.get("needs_graph"):
            try:
                graph = self.graph_retriever.ask(standalone)
                result.graph_debug = {
                    "cypher_query": graph.get("cypher_query", ""),
                    "cypher_source": graph.get("cypher_source", ""),
                    "record_count": graph.get("record_count", 0),
                    "graph_intent": graph.get("graph_intent", "free_form"),
                    "requested_outputs": graph.get("requested_outputs", []),
                    "returned_columns": graph.get("returned_columns", []),
                    "completeness_valid": graph.get("completeness_valid"),
                    "missing_outputs": graph.get("missing_outputs", []),
                    "query_count": graph.get("query_count", 1),
                    "follow_up_query_required": graph.get("follow_up_query_required", False),
                }
                if graph.get("context"):
                    result.graph_context = (
                        f"Query executed: {graph.get('cypher_query', '')}\n\n{graph['context']}"
                    )
                    result.sources.extend(graph.get("sources", []))
            except Exception as exc:
                logger.error("Graph retrieval failed: %s", exc)
                result.graph_debug = {"error": str(exc)}
        result.sources = filter_sources(result.sources)
        return result

    def _generate_conversation(self, question: str, state: ConversationState, profile: ModelProfile) -> str:
        try:
            response = call_with_retries(
                _chat_callable([
                    {"role": "system", "content": CONVERSATION_PROMPT},
                    *state.messages[-4:],
                    {"role": "user", "content": question},
                ], profile.final_model, 0.2),
                op_name="conversation response",
            )
            return response.choices[0].message.content or "متوجه شدم."
        except (LLMConfigError, LLMRequestError):
            return "خواهش می‌کنم. اگر پرسش فنی دیگری دارید، بپرسید."

    def generate(
        self,
        question: str,
        understanding: dict,
        retrieval: RetrievalResult,
        state: ConversationState,
        profile: ModelProfile,
    ) -> str:
        if not retrieval.has_any_context:
            if understanding.get("needs_graph") and retrieval.graph_debug.get("error"):
                return (
                    "پرس‌وجوی Neo4j با خطا مواجه شد و هیچ نتیجه‌ای از گراف ساختمان دریافت نشد. "
                    "هیچ مقدار یا واقعیت گرافی حدس زده نشد."
                )
            if understanding.get("needs_graph") and not understanding.get("needs_vector"):
                return "پرس‌وجوی Neo4j اجرا شد، اما هیچ رکورد منطبق در گراف فعلی ساختمان پیدا نشد."
            return (
                "پس از بازنویسی پرسش و جست‌وجوی تکمیلی، شواهد مرتبط و قابل استنادی "
                "در منابع موجود پیدا نشد. لطفاً نام سند، تجهیز یا بخش موردنظر را دقیق‌تر مشخص کنید."
            )
        prompt = COMBINE_PROMPT.format(
            vector_context=retrieval.vector_context or "No regulatory context retrieved.",
            graph_context=retrieval.graph_context or "No building graph context retrieved.",
            conversation_context=_history_text(state),
            standalone_query=understanding["standalone_query"],
            question=question,
        )
        try:
            response = call_with_retries(
                _chat_callable([{"role": "user", "content": prompt}], profile.final_model),
                op_name="grounded final answer",
            )
            return response.choices[0].message.content or ""
        except (LLMConfigError, LLMRequestError) as exc:
            logger.error("Final model unavailable; returning bounded extractive evidence: %s", exc)
            return self._extractive_answer(retrieval)

    @staticmethod
    def _extractive_answer(retrieval: RetrievalResult) -> str:
        """Last-resort grounded output; never turns provider failure into 500."""
        parts = ["مدل تولید پاسخ در دسترس نبود؛ متن‌های مرتبط منبع بدون تفسیر ارائه می‌شوند:"]
        if retrieval.regulation:
            for chunk in retrieval.regulation.chunks:
                clause = str(chunk.metadata.get("clause_id", "unknown"))
                page = chunk.metadata.get("page_number")
                if clause in {"", "unknown", "None"} or not page:
                    continue
                parts.append(f"{chunk.text} [Clause {clause}, Page {page}]")
        if retrieval.graph_context:
            parts.append(retrieval.graph_context)
        return "\n\n".join(parts)

    def _repair_citations(
        self,
        answer: str,
        question: str,
        retrieval: RetrievalResult,
        profile: ModelProfile,
    ) -> str:
        prompt = (
            f"{CITATION_REPAIR_PROMPT}\n\nQuestion:\n{question}\n\nEvidence:\n"
            f"{retrieval.vector_context}\n\nDraft:\n{answer}"
        )
        response = call_with_retries(
            _chat_callable([{"role": "user", "content": prompt}], profile.final_model),
            op_name="citation repair",
        )
        return response.choices[0].message.content or ""

    def ask(
        self,
        question: str,
        conversation_id: str | None = None,
        use_strong_models: bool = False,
        source_override: str | None = None,
    ) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("Question cannot be empty.")
        profile = get_model_profile(use_strong_models, self.settings)
        state = self.memory.get(conversation_id)
        understanding = self.understand(question, state, profile)
        if source_override == "vector":
            understanding.update(needs_vector=True, needs_graph=False, is_technical=True, intent="technical")
        elif source_override == "graph":
            understanding.update(needs_vector=False, needs_graph=True, is_technical=True, intent="technical")
        logger.info(
            "RAG mode=%s router=%s final=%s original=%r rewritten=%r vector=%s graph=%s follow_up=%s complete=%s",
            profile.mode, profile.router_model, profile.final_model, question,
            understanding["standalone_query"], understanding.get("needs_vector"),
            understanding.get("needs_graph"), understanding.get("is_follow_up"),
            understanding.get("completeness_requested"),
        )

        if understanding.get("intent") == "conversation" and not (
            understanding.get("needs_vector") or understanding.get("needs_graph")
        ):
            answer = self._generate_conversation(question, state, profile)
            self.memory.append_turn(state, question, answer, topic=state.topic)
            return {
                "answer": answer, "sources": [], "used_vector": False, "used_graph": False,
                "conversation_id": state.conversation_id, "model_mode": profile.mode,
                "router_model": profile.router_model, "final_model": profile.final_model,
                "rewritten_query": understanding["standalone_query"],
                "retrieval_debug": {"intent": "conversation"},
            }

        # Free-form Cypher generation is part of query understanding. In strong
        # mode it therefore uses the configured Gemini/router model as well.
        if hasattr(self.graph_retriever, "cypher_gen"):
            self.graph_retriever.cypher_gen.model = profile.router_model
        retrieval = self.retrieve(understanding)
        logger.info(
            "RAG citation scope: mode=%s router=%s final=%s retrieved_chunks=%d allowed_citations=%s",
            profile.mode, profile.router_model, profile.final_model,
            len(retrieval.regulation.chunks) if retrieval.regulation else 0,
            sorted(supported_regulation_pairs(retrieval.sources)),
        )
        answer = self.generate(question, understanding, retrieval, state, profile)
        logger.info(
            "Generated citation diagnostics: recognized=%s citation_like=%s",
            [(item.clause, item.page) for item in extract_regulation_citations(answer)],
            citation_like_fragments(answer),
        )
        answer = canonicalize_generated_citations(answer)
        requires_citation = bool(retrieval.vector_context)
        if not validate_generated_answer(answer, retrieval.sources, requires_citation):
            try:
                answer = self._repair_citations(answer, question, retrieval, profile)
                answer = canonicalize_generated_citations(answer)
            except (LLMConfigError, LLMRequestError) as exc:
                logger.error("Citation repair failed: %s", exc)
        if not validate_generated_answer(answer, retrieval.sources, requires_citation):
            answer = (
                "متن مرتبط بازیابی شد، اما پاسخ تولیدشده ارجاع قابل‌اعتبارسنجی نداشت؛ "
                "برای جلوگیری از ارائه ادعای بدون منبع، پاسخ نمایش داده نشد."
            )
            answer_sources: list[dict] = []
        else:
            answer_sources = sources_used_by_answer(answer, retrieval.sources)

        chunk_ids = [chunk.chunk_id for chunk in (retrieval.regulation.chunks if retrieval.regulation else [])]
        self.memory.append_turn(
            state,
            question,
            answer,
            topic=str(understanding.get("topic", "")),
            chunk_ids=chunk_ids,
            needs_vector=bool(understanding.get("needs_vector")),
            needs_graph=bool(understanding.get("needs_graph")),
        )
        diagnostics = asdict(retrieval.regulation.diagnostics) if retrieval.regulation else {}
        diagnostics.update({
            "intent": understanding.get("intent"),
            "is_follow_up": understanding.get("is_follow_up"),
            "completeness_requested": understanding.get("completeness_requested"),
            "routing": {
                "vector": bool(understanding.get("needs_vector")),
                "graph": bool(understanding.get("needs_graph")),
            },
            "graph": retrieval.graph_debug,
        })
        return {
            "answer": answer,
            "sources": answer_sources,
            "used_vector": bool(retrieval.vector_context),
            "used_graph": bool(retrieval.graph_context),
            "conversation_id": state.conversation_id,
            "model_mode": profile.mode,
            "router_model": profile.router_model,
            "final_model": profile.final_model,
            "rewritten_query": understanding["standalone_query"],
            "retrieval_debug": diagnostics,
        }
