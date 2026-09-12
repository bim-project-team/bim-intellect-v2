"""Validated document classification shared by ingestion and retrieval."""

from __future__ import annotations

from dataclasses import dataclass

DOCUMENT_DOMAINS = frozenset({"regulation", "sustainability", "leed", "standard"})
SUSTAINABILITY_DOCUMENT_DOMAINS = ("leed", "sustainability", "standard")


@dataclass(frozen=True)
class DocumentClassification:
    document_domain: str = "regulation"
    standard_name: str = ""
    standard_version: str = ""


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
