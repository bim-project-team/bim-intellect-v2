"""Conversation-aware orchestration across regulation and BIM graph knowledge."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from bim_graph.graph_retriever import GraphRetriever
from sustainability.assessment import LeedAssessment, assess_available_evidence
from sustainability.retriever import SustainabilityEvidence, SustainabilityEvidenceRetriever

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
from .document_metadata import SUSTAINABILITY_DOCUMENT_DOMAINS, metadata_domain, normalize_domain_filter

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
    r"(?:مقررات|ضوابط|استاندارد|الزام|مطابق|سند|منبع|LEED|پایداری|regulation|code|compliance|sustainab)",
    re.IGNORECASE,
)
_LEED_DOCUMENT_PATTERN = re.compile(
    r"(?:\bLEED\b|استاندارد\s+لید|معیار\s+LEED|اعتبار\s+LEED|"
    r"(?:sustainability|sustainable|پایداری).{0,35}(?:requirement|standard|guidance|document|الزام|استاندارد|سند))",
    re.IGNORECASE,
)
_SUSTAINABILITY_DATA_PATTERN = re.compile(
    r"(?:kg\s*co2e|kgco₂e|embodied\s+carbon|carbon\s+(?:footprint|contribution|result)|"
    r"material\s+sustainability|sustainable\s+materials?|environmental\s+impact|"
    r"کربن\s+(?:نهفته|تجسم[‌-]?یافته|مجسم|محاسبه|برآورد|سهم)|سهم\s+کربن|ردپای\s+کربن|"
    r"مصالح\s+پایدار|پایداری\s+مصالح|اثر(?:ات)?\s+زیست[‌-]?محیطی)",
    re.IGNORECASE,
)
_PROJECT_EVIDENCE_PATTERN = re.compile(
    r"(?:\bproject\b|\bmodel\b|\bBIM\b|\bIFC\b|\belement|material.{0,20}(?:largest|contribut)|"
    r"پروژه|مدل|عنصر|مصالح|بیشترین\s+سهم|فایل\s+IFC|اطلاعات\s+موجود)",
    re.IGNORECASE,
)
_LEED_ASSESSMENT_PATTERN = re.compile(
    r"(?:\b(?:evaluate|assess|satisf(?:y|ied)|evidence|enough|sufficient|available)\b|"
    r"بررسی|ارزیابی|سنجش|کافی|شواهد|اطلاعات\s+موجود|برآورده|تأمین|تامین)",
    re.IGNORECASE,
)
_ORDINARY_REGULATION_PATTERN = re.compile(
    r"(?:\b(?:regulations?|building\s+code|construction\s+code)\b|مقررات|ضوابط|آیین[‌-]?نامه)",
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
    routed.setdefault("needs_sustainability", False)
    routed.setdefault("needs_leed_assessment", False)
    routed.setdefault("is_technical", False)
    routed.setdefault("confidence", None)
    routed.setdefault("reasoning", "No routing explanation was provided.")
    for key in (
        "needs_vector", "needs_graph", "needs_sustainability",
        "needs_leed_assessment", "is_technical",
    ):
        if not isinstance(routed[key], bool):
            raise ValueError(f"Router field {key!r} must be a boolean.")
    domains = routed.get("document_domains") or []
    if not isinstance(domains, list):
        raise ValueError("Router field 'document_domains' must be a list.")
    routed["document_domains"] = list(normalize_domain_filter(domains))
    routed.setdefault("sustainability_intent", "general")
    project_evidence_signal = bool(_PROJECT_EVIDENCE_PATTERN.search(question))
    sustainability_topic_signal = bool(_SUSTAINABILITY_DATA_PATTERN.search(question))
    leed_document_signal = bool(_LEED_DOCUMENT_PATTERN.search(question))
    sustainability_document_signal = bool(
        leed_document_signal or (sustainability_topic_signal and not project_evidence_signal)
    )
    sustainability_data_signal = bool(
        (sustainability_topic_signal and project_evidence_signal)
        or (
            leed_document_signal and project_evidence_signal
            and _LEED_ASSESSMENT_PATTERN.search(question)
        )
    )
    if sustainability_document_signal:
        routed["needs_vector"] = True
        domains = set(SUSTAINABILITY_DOCUMENT_DOMAINS)
        if _ORDINARY_REGULATION_PATTERN.search(question):
            domains.add("regulation")
        routed["document_domains"] = sorted(domains)
        routed["is_technical"] = True
    if sustainability_data_signal:
        routed["needs_sustainability"] = True
        routed["is_technical"] = True
    if leed_document_signal and sustainability_data_signal:
        routed["needs_leed_assessment"] = True
    if routed["document_domains"]:
        routed["needs_vector"] = True
    if explicitly_requests_graph_context(question):
        routed["needs_graph"] = True
        routed["is_technical"] = True
        if not _REGULATION_INTENT_PATTERN.search(question):
            routed["needs_vector"] = False
        routed["reasoning"] = "Neo4j retrieval required by explicit IFC/schema/property vocabulary."
    if explicitly_requests_vector_context(question) and not routed["needs_vector"]:
        routed["needs_vector"] = True
        routed["reasoning"] = "Vector retrieval required because the question explicitly refers to PDF knowledge."
    elif routed["is_technical"] and not (
        routed["needs_vector"] or routed["needs_graph"] or routed["needs_sustainability"]
    ):
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
    sustainability_context: str = ""
    sustainability: SustainabilityEvidence | None = None
    sustainability_debug: dict[str, Any] = field(default_factory=dict)
    assessment_context: str = ""
    assessment: LeedAssessment | None = None

    @property
    def has_any_context(self) -> bool:
        return bool(
            self.vector_context or self.graph_context
            or self.sustainability_context or self.assessment_context
        )


def valid_source(source: dict) -> bool:
    clause = str(source.get("clause_id", "")).strip().lower()
    document_id = str(source.get("document_id", "")).strip().lower()
    section_id = str(source.get("section_id", "")).strip().lower()
    has_clause = clause not in {"", "unknown", "none", "null"}
    has_document_section = (
        document_id not in {"", "unknown", "none", "null"}
        and section_id not in {"", "unknown", "none", "null"}
    )
    return source.get("page_number") is not None and (has_clause or has_document_section)


def deduplicate_sources(sources: list[dict]) -> list[dict]:
    result, seen = [], set()
    for source in sources:
        if source.get("type", "regulation") == "regulation":
            key = (
                "regulation", source.get("document_id") or source.get("source"),
                source.get("clause_id"), source.get("section_id"), source.get("page_number"),
            )
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

DOCUMENT_CITATION_PATTERN = re.compile(
    rf"\[Document\s+(?P<document>[^,\]\r\n]+)\s*,\s*"
    rf"Section\s+(?P<section>[^,\]\r\n]+)\s*,\s*"
    rf"Page\s+(?P<page>{_DIGITS}+)\s*\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Citation:
    clause: str
    page: int
    document: str | None = None


@dataclass(frozen=True)
class DocumentCitation:
    document_id: str
    section_id: str
    page: int


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


def extract_document_citations(answer: str) -> list[DocumentCitation]:
    return [
        DocumentCitation(
            match.group("document").strip(), match.group("section").strip(),
            int(_normalize_digits(match.group("page"))),
        )
        for match in DOCUMENT_CITATION_PATTERN.finditer(answer or "")
    ]


def canonicalize_generated_citations(answer: str) -> str:
    """Render recognized equivalent citations in the public canonical syntax."""
    def replace(match: re.Match) -> str:
        clause = match.group("bracket_clause") or match.group("label_clause")
        page = match.group("bracket_page") or match.group("label_page")
        return f"[Clause {_normalize_clause(clause)}, Page {int(_normalize_digits(page))}]"

    answer = CITATION_PATTERN.sub(replace, answer or "")

    def replace_document(match: re.Match) -> str:
        return (
            f"[Document {match.group('document').strip()}, "
            f"Section {match.group('section').strip()}, "
            f"Page {int(_normalize_digits(match.group('page')))}]"
        )

    return DOCUMENT_CITATION_PATTERN.sub(replace_document, answer)


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
        for source in sources
        if source.get("type", "regulation") == "regulation"
        and str(source.get("clause_id", "")).strip().lower() not in {"", "unknown", "none", "null"}
        and valid_source(source)
    }


def cited_document_triplets(answer: str) -> set[tuple[str, str, str]]:
    return {
        (item.document_id, item.section_id, str(item.page))
        for item in extract_document_citations(answer)
    }


def supported_document_triplets(sources: list[dict]) -> set[tuple[str, str, str]]:
    return {
        (
            str(source.get("document_id", "")).strip(),
            str(source.get("section_id", "")).strip(),
            _normalize_digits(source.get("page_number", "")).strip(),
        )
        for source in sources
        if source.get("type", "regulation") == "regulation" and valid_source(source)
    }


def validate_generated_answer(answer: str, sources: list[dict], requires_regulatory_citation: bool = False) -> bool:
    cited, supported = cited_regulation_pairs(answer), supported_regulation_pairs(sources)
    cited_documents = cited_document_triplets(answer)
    supported_documents = supported_document_triplets(sources)
    invalid = cited - supported
    invalid_documents = cited_documents - supported_documents
    if invalid or invalid_documents:
        logger.warning(
            "Citation validation failed: generated=%s generated_documents=%s invalid=%s "
            "invalid_documents=%s reason=not_in_current_retrieved_sources",
            sorted(cited), sorted(cited_documents), sorted(invalid), sorted(invalid_documents),
        )
        return False
    if requires_regulatory_citation and not (cited or cited_documents):
        logger.warning(
            "Citation validation failed: generated=[] allowed=%s reason=no_recognizable_regulatory_citation",
            sorted(supported),
        )
        return False
    logger.info("Citation validation result=valid generated=%s allowed=%s", sorted(cited), sorted(supported))
    return True


def sources_used_by_answer(answer: str, sources: list[dict]) -> list[dict]:
    cited = cited_regulation_pairs(answer)
    cited_documents = cited_document_triplets(answer)
    return [
        source for source in sources
        if source.get("type", "regulation") != "regulation"
        or (_normalize_clause(source.get("clause_id", "")), _normalize_digits(source.get("page_number", "")).strip()) in cited
        or (
            str(source.get("document_id", "")).strip(),
            str(source.get("section_id", "")).strip(),
            _normalize_digits(source.get("page_number", "")).strip(),
        ) in cited_documents
    ]


_SUSTAINABILITY_NUMERIC_LINE = re.compile(
    r"(?:carbon|co2e|co₂e|factor|quantity|volume|area|mass|kg\b|m[²³23]\b|"
    r"کربن|ضریب|مقدار|حجم|مساحت|جرم|کیلوگرم|متر\s*(?:مربع|مکعب))",
    re.IGNORECASE,
)
_NUMBER_PATTERN = re.compile(r"(?<![\w-])[-+]?\d+(?:[.,]\d+)?")


def _normalized_numbers(text: str) -> set[str]:
    normalized = _normalize_digits(text or "")
    output: set[str] = set()
    for raw in _NUMBER_PATTERN.findall(normalized):
        try:
            output.add(format(float(raw.replace(",", ".")), ".15g"))
        except ValueError:
            continue
    return output


def validate_sustainability_numeric_claims(
    answer: str,
    sustainability_context: str,
    document_context: str = "",
) -> bool:
    """Reject numeric sustainability claims absent from deterministic/retrieved evidence."""
    deterministic_numbers = _normalized_numbers(sustainability_context)
    documentary_numbers = _normalized_numbers(document_context)
    claimed: set[str] = set()
    invalid: set[str] = set()
    project_value_pattern = re.compile(
        r"(?:\b(?:project|building|model|BIM|IFC|calculated|estimated|total|"
        r"contribution|result)\b|پروژه|ساختمان|مدل|محاسبه|برآورد|مجموع|سهم|نتیجه)",
        re.IGNORECASE,
    )
    for original_line in (answer or "").splitlines():
        has_document_citation = bool(
            CITATION_PATTERN.search(original_line) or DOCUMENT_CITATION_PATTERN.search(original_line)
        )
        line = CITATION_PATTERN.sub("", original_line)
        line = DOCUMENT_CITATION_PATTERN.sub("", line)
        if _SUSTAINABILITY_NUMERIC_LINE.search(line):
            line_claims = _normalized_numbers(line)
            claimed.update(line_claims)
            # Cited standards may state numeric requirements. Project values,
            # estimates and calculated results must still come only from the
            # deterministic sustainability evidence, never from a PDF number.
            allowed = deterministic_numbers
            if has_document_citation and not project_value_pattern.search(line):
                allowed = deterministic_numbers | documentary_numbers
            invalid.update(line_claims - allowed)
    if invalid:
        logger.warning(
            "Sustainability numeric grounding failed: claimed=%s invalid=%s",
            sorted(claimed), sorted(invalid),
        )
        return False
    return True


_PROJECT_CERTIFICATION_CLAIM = re.compile(
    r"(?:\b(?:project|building)\b.{0,35}\b(?:is|achieves?|earns?|qualifies?\s+for|certified\s+as)\b"
    r".{0,20}\bLEED\s+(?:Certified|Silver|Gold|Platinum)\b|"
    r"(?:پروژه|ساختمان).{0,35}(?:دارای|کسب|واجد|گواهی).{0,25}(?:LEED|لید).{0,15}(?:طلایی|نقره[‌-]?ای|پلاتین|گواهی))",
    re.IGNORECASE,
)


def validate_leed_assessment_language(answer: str, assessment: LeedAssessment | None) -> bool:
    if _PROJECT_CERTIFICATION_CLAIM.search(answer or ""):
        logger.warning("Prohibited project LEED certification claim rejected.")
        return False
    mentioned = {
        status for status in (
            "satisfied_from_available_evidence",
            "not_satisfied_from_available_evidence",
            "insufficient_evidence",
            "not_automatically_evaluable",
        )
        if re.search(rf"(?<![a-z_]){re.escape(status)}(?![a-z_])", answer or "")
    }
    if mentioned and (assessment is None or mentioned != {assessment.status}):
        logger.warning("Generated assessment state does not match deterministic state: %s", sorted(mentioned))
        return False
    return True


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
            "needs_vector": False, "needs_graph": False, "needs_sustainability": False,
            "needs_leed_assessment": False, "document_domains": [],
            "sustainability_intent": "general", "is_technical": False,
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
        needs_sustainability = bool(state.last_needs_sustainability)
        document_domains = list(state.last_document_domains)
    else:
        graph_signal = explicitly_requests_graph_context(question) or bool(re.search(
            r"(?:\bclash\b|\bclearance\b|کلش|تداخل|مدل\s*(?:ساختمان|بیم)?|عنصر\s*(?:شماره)?|این\s+(?:لوله|دیوار|تیر|عنصر))",
            normalized,
        ))
        compliance_signal = bool(re.search(
            r"(?:مقررات|ضوابط|استاندارد|مطابق|مجاز|الزام|compliance|regulation|code)",
            normalized,
        ))
        project_signal = bool(_PROJECT_EVIDENCE_PATTERN.search(question))
        sustainability_topic_signal = bool(_SUSTAINABILITY_DATA_PATTERN.search(question))
        leed_signal = bool(_LEED_DOCUMENT_PATTERN.search(question))
        sustainability_document_signal = bool(
            leed_signal or (sustainability_topic_signal and not project_signal)
        )
        sustainability_signal = bool(
            (sustainability_topic_signal and project_signal)
            or (leed_signal and project_signal and _LEED_ASSESSMENT_PATTERN.search(question))
        )
        needs_graph = graph_signal
        needs_sustainability = sustainability_signal
        needs_vector = (
            compliance_signal or sustainability_document_signal
            or not (graph_signal or sustainability_signal)
        )
        document_domains = (
            list(SUSTAINABILITY_DOCUMENT_DOMAINS) if sustainability_document_signal else []
        )
    return {
        "standalone_query": standalone,
        "retrieval_queries": [standalone],
        "needs_vector": needs_vector,
        "needs_graph": needs_graph,
        "needs_sustainability": needs_sustainability,
        "needs_leed_assessment": bool(needs_sustainability and document_domains),
        "document_domains": document_domains,
        "sustainability_intent": "general",
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
        sustainability_retriever: SustainabilityEvidenceRetriever | None = None,
    ):
        self.settings = settings
        self.memory = memory
        self.graph_retriever = graph_retriever or GraphRetriever()
        self.sustainability_retriever = sustainability_retriever or SustainabilityEvidenceRetriever()
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
                "needs_vector": True, "needs_graph": True,
                "needs_sustainability": False, "needs_leed_assessment": False,
                "document_domains": [], "sustainability_intent": "general",
                "is_technical": True,
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
        decision.setdefault("needs_sustainability", False)
        decision.setdefault("needs_leed_assessment", False)
        decision.setdefault("document_domains", [])
        decision.setdefault("sustainability_intent", "general")
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
                document_domains=understanding.get("document_domains"),
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
        if understanding.get("needs_sustainability"):
            try:
                sustainability = self.sustainability_retriever.retrieve(
                    standalone,
                    project_id=understanding.get("project_id"),
                    file_ids=understanding.get("file_ids"),
                    intent=understanding.get("sustainability_intent"),
                )
                result.sustainability = sustainability
                result.sustainability_debug = dict(sustainability.debug)
                if sustainability.available:
                    result.sustainability_context = sustainability.context
                    if sustainability.source:
                        result.sources.append(sustainability.source)
            except Exception as exc:
                logger.error("Sustainability retrieval failed: %s", exc)
                result.sustainability_debug = {"status": "error", "message": str(exc)}

        sustainability_domains = set(SUSTAINABILITY_DOCUMENT_DOMAINS)
        has_sustainability_document = bool(
            result.regulation and any(
                metadata_domain(chunk.metadata) in sustainability_domains
                for chunk in result.regulation.chunks
            )
        )
        if understanding.get("needs_leed_assessment") or (
            understanding.get("needs_sustainability")
            and sustainability_domains.intersection(understanding.get("document_domains") or [])
        ):
            result.assessment = assess_available_evidence(
                has_bim_sustainability_evidence=bool(
                    result.sustainability and result.sustainability.available
                ),
                has_document_evidence=has_sustainability_document,
            )
            result.assessment_context = (
                "<LEED_ORIENTED_ASSESSMENT deterministic=\"true\">\n"
                + json.dumps(result.assessment.to_dict(), ensure_ascii=False, sort_keys=True)
                + "\nThis is not a LEED certification or credit award.\n"
                "</LEED_ORIENTED_ASSESSMENT>"
            )
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
            if understanding.get("needs_sustainability"):
                message = retrieval.sustainability_debug.get("message")
                return (
                    "No deterministic sustainability evidence is available for the requested project/file scope. "
                    + (str(message) if message else "Run sustainability analysis for that scope first.")
                )
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
            sustainability_context=(
                retrieval.sustainability_context
                or "No deterministic project sustainability context retrieved."
            ),
            assessment_context=(
                retrieval.assessment_context
                or "No LEED-oriented assessment state was requested."
            ),
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
                if not page:
                    continue
                if clause not in {"", "unknown", "None"}:
                    citation = f"[Clause {clause}, Page {page}]"
                else:
                    document_id = str(chunk.metadata.get("document_id", "unknown"))
                    section_id = str(chunk.metadata.get("section_id", "unknown"))
                    if "unknown" in {document_id, section_id}:
                        continue
                    citation = (
                        f"[Document {document_id}, Section {section_id}, Page {page}]"
                    )
                parts.append(f"{chunk.text} {citation}")
        if retrieval.graph_context:
            parts.append(retrieval.graph_context)
        if retrieval.sustainability and retrieval.sustainability.available:
            parts.append(retrieval.sustainability.display_text)
        if retrieval.assessment:
            parts.append(
                f"LEED-oriented assessment: {retrieval.assessment.status}. "
                f"{retrieval.assessment.explanation}"
            )
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
        project_id: str | None = None,
        file_ids: list[str] | None = None,
    ) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("Question cannot be empty.")
        profile = get_model_profile(use_strong_models, self.settings)
        state = self.memory.get(conversation_id)
        understanding = self.understand(question, state, profile)
        if source_override == "vector":
            understanding.update(
                needs_vector=True, needs_graph=False, needs_sustainability=False,
                needs_leed_assessment=False, is_technical=True, intent="technical",
            )
        elif source_override == "graph":
            understanding.update(
                needs_vector=False, needs_graph=True, needs_sustainability=False,
                needs_leed_assessment=False, document_domains=[],
                is_technical=True, intent="technical",
            )
        understanding["project_id"] = project_id
        understanding["file_ids"] = list(file_ids) if file_ids else None
        logger.info(
            "RAG mode=%s router=%s final=%s original=%r rewritten=%r vector=%s graph=%s sustainability=%s domains=%s follow_up=%s complete=%s",
            profile.mode, profile.router_model, profile.final_model, question,
            understanding["standalone_query"], understanding.get("needs_vector"),
            understanding.get("needs_graph"), understanding.get("needs_sustainability"),
            understanding.get("document_domains"), understanding.get("is_follow_up"),
            understanding.get("completeness_requested"),
        )

        if understanding.get("intent") == "conversation" and not (
            understanding.get("needs_vector") or understanding.get("needs_graph")
            or understanding.get("needs_sustainability")
        ):
            answer = self._generate_conversation(question, state, profile)
            self.memory.append_turn(state, question, answer, topic=state.topic)
            return {
                "answer": answer, "sources": [], "used_vector": False, "used_graph": False,
                "used_sustainability": False, "assessment": None,
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

        sustainability_numbers_valid = (
            not understanding.get("needs_sustainability")
            or validate_sustainability_numeric_claims(
                answer, retrieval.sustainability_context, retrieval.vector_context,
            )
        )
        leed_language_valid = validate_leed_assessment_language(answer, retrieval.assessment)
        if not sustainability_numbers_valid or not leed_language_valid:
            answer = canonicalize_generated_citations(self._extractive_answer(retrieval))
            if (
                validate_generated_answer(answer, retrieval.sources, requires_citation)
                and validate_leed_assessment_language(answer, retrieval.assessment)
            ):
                answer_sources = sources_used_by_answer(answer, retrieval.sources)
            else:
                answer = (
                    "The generated sustainability answer contained an unsupported numeric value and "
                    "the safe extractive fallback could not satisfy citation validation."
                )
                answer_sources = []

        if retrieval.assessment and hasattr(self.memory, "record_sustainability_finding"):
            sustainability_data = (
                retrieval.sustainability.data
                if retrieval.sustainability and retrieval.sustainability.available
                else {}
            )
            scope = sustainability_data.get("scope", {})
            missing_project_data = list(
                (sustainability_data.get("excluded_elements") or {}).keys()
            )
            if not retrieval.assessment.has_bim_sustainability_evidence:
                missing_project_data.append("deterministic_sustainability_run")
            document_sources = [
                dict(source) for source in answer_sources
                if source.get("type", "regulation") == "regulation"
                and metadata_domain(source) in set(SUSTAINABILITY_DOCUMENT_DOMAINS)
            ]
            self.memory.record_sustainability_finding(state, {
                "criterion": question,
                "assessment_status": retrieval.assessment.status,
                "assessment_explanation": retrieval.assessment.explanation,
                "evidence": answer,
                "project_id": scope.get("project_id") or project_id,
                "file_ids": scope.get("file_ids") or list(file_ids or []),
                "sustainability_run_id": scope.get("run_id"),
                "document_citations": document_sources,
                "missing_project_data": sorted(set(missing_project_data)),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "session_only": True,
            })

        chunk_ids = [chunk.chunk_id for chunk in (retrieval.regulation.chunks if retrieval.regulation else [])]
        self.memory.append_turn(
            state,
            question,
            answer,
            topic=str(understanding.get("topic", "")),
            chunk_ids=chunk_ids,
            needs_vector=bool(understanding.get("needs_vector")),
            needs_graph=bool(understanding.get("needs_graph")),
            needs_sustainability=bool(understanding.get("needs_sustainability")),
            document_domains=understanding.get("document_domains") or [],
        )
        diagnostics = asdict(retrieval.regulation.diagnostics) if retrieval.regulation else {}
        diagnostics.update({
            "intent": understanding.get("intent"),
            "is_follow_up": understanding.get("is_follow_up"),
            "completeness_requested": understanding.get("completeness_requested"),
            "routing": {
                "vector": bool(understanding.get("needs_vector")),
                "graph": bool(understanding.get("needs_graph")),
                "sustainability": bool(understanding.get("needs_sustainability")),
                "document_domains": list(understanding.get("document_domains") or []),
            },
            "graph": retrieval.graph_debug,
            "sustainability": retrieval.sustainability_debug,
            "assessment": retrieval.assessment.to_dict() if retrieval.assessment else None,
        })
        return {
            "answer": answer,
            "sources": answer_sources,
            "used_vector": bool(retrieval.vector_context),
            "used_graph": bool(retrieval.graph_context),
            "used_sustainability": bool(
                retrieval.sustainability and retrieval.sustainability.available
            ),
            "assessment": retrieval.assessment.to_dict() if retrieval.assessment else None,
            "conversation_id": state.conversation_id,
            "model_mode": profile.mode,
            "router_model": profile.router_model,
            "final_model": profile.final_model,
            "rewritten_query": understanding["standalone_query"],
            "retrieval_debug": diagnostics,
        }
