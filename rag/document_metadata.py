"""Validated document classification shared by ingestion and retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

DOCUMENT_DOMAINS = frozenset({"regulation", "sustainability", "leed", "standard"})
SUSTAINABILITY_DOCUMENT_DOMAINS = ("leed", "sustainability", "standard")


@dataclass(frozen=True)
class DocumentClassification:
    document_domain: str = "regulation"
    standard_name: str = ""
    standard_version: str = ""


@dataclass(frozen=True)
class DocumentPageCountAnswer:
    answer: str
    source: dict[str, Any]


_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
)
_PAGE_COUNT_PATTERN = re.compile(
    r"(?:چند\s*صفحه|تعداد\s*صفحات|تعداد\s*صفحه|page\s*count|how\s+many\s+pages)",
    re.IGNORECASE,
)


def resolve_document_page_count(
    question: str,
    documents: list[dict[str, Any]],
) -> DocumentPageCountAnswer | None:
    """Answer total-page metadata questions without inferring from chunks."""
    normalized = (question or "").translate(_DIGIT_TRANSLATION)
    if not _PAGE_COUNT_PATTERN.search(normalized):
        return None
    chapter_match = re.search(r"(?:مبحث|mabhas)\s*(\d{1,2})", normalized, re.IGNORECASE)
    candidates = [item for item in documents if int(item.get("total_pdf_pages") or 0) > 0]
    if chapter_match:
        requested = chapter_match.group(1)
        candidates = [
            item for item in candidates
            if str(item.get("document_number") or "") == requested
            or bool(re.match(rf"^\s*{re.escape(requested)}(?:\D|$)", str(item.get("filename", ""))))
        ]
    if len(candidates) != 1:
        return None
    document = candidates[0]
    total = int(document["total_pdf_pages"])
    filename = str(document.get("filename") or document.get("document_id") or "سند")
    answer = (
        f"فایل «{filename}» در مجموع {total} صفحه فیزیکی PDF دارد. "
        "این عدد از فراداده خود PDF خوانده شده است، نه از بیشترین صفحه بازیابی‌شده."
    )
    return DocumentPageCountAnswer(answer, {
        "type": "document_metadata",
        "source": filename,
        "document_id": document.get("document_id"),
        "document_number": document.get("document_number") or "",
        "total_pdf_pages": total,
        "page_number_convention": "physical_pdf_page_1_based",
    })


def normalize_document_domain(value: str | None) -> str:
    domain = (value or "regulation").strip().casefold()
    if domain not in DOCUMENT_DOMAINS:
        raise ValueError(
            f"Unsupported document_domain {value!r}; expected one of: "
            + ", ".join(sorted(DOCUMENT_DOMAINS))
        )
    return domain


def classify_document(
    document_domain: str | None = None,
    standard_name: str | None = None,
    standard_version: str | None = None,
) -> DocumentClassification:
    domain = normalize_document_domain(document_domain)
    name = (standard_name or "").strip()
    version = (standard_version or "").strip()
    if domain == "leed" and not name:
        name = "LEED"
    if domain in {"leed", "standard"} and not version:
        raise ValueError(f"standard_version is required for document_domain={domain!r}.")
    if domain == "standard" and not name:
        raise ValueError("standard_name is required for document_domain='standard'.")
    return DocumentClassification(domain, name, version)


def metadata_domain(metadata: dict) -> str:
    """Older chunks predate classification and remain ordinary regulations."""
    try:
        return normalize_document_domain(str(metadata.get("document_domain") or "regulation"))
    except ValueError:
        return "regulation"


def normalize_domain_filter(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(dict.fromkeys(normalize_document_domain(value) for value in values))
