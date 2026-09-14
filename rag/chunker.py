"""
Chunks regulatory PDFs into structure-aware text segments for embedding.
Each chunk retains traceable clause/page metadata and continuation links.

Supports multiple regulation PDFs, not just a single hardcoded document.
Each PDF can use a different top-level chapter/clause number (e.g. Mabhas 15
clauses look like "15-1-2", Mabhas 4 clauses look like "4-2-1") - the clause
root number is auto-detected per document instead of being hardcoded, so
the same chunker works across different regulation PDFs without editing
anything by hand for each one.

This version merges two prior iterations:

1. Multi-document support (auto-detecting the clause root per PDF via
   detect_clause_root(), carrying a `source` label on each Chunk) so the
   same code works across Mabhas 15, Mabhas 4, etc. without hardcoding.
2. More accurate clause detection/segmentation (originally prototyped in
   chunker_robust.py):
   - Pages are split into per-clause SEGMENTS before bounded chunking
     happens. Previously, chunk boundaries were character-based only, so
     a single raw window could start under clause 15-1-2 and run
     into the text of 15-1-3 - guess_clause_id() would then (correctly,
     but confusingly) label the whole window with whichever clause number
     appeared *last* in the window, silently mis-attributing the leading
     text. Segmenting on clause boundaries first means each chunk can only
     ever belong to the clause it actually started in.
   - Clause numbers are also matched in reversed/RTL-extracted form
     ("-2-1-15" as well as "15-1-2"), since pypdf sometimes extracts
     Persian/Arabic-adjacent text with the numeric segments reversed.
   - Overlapping matches (e.g. a standard-form and reversed-form match on
     the same digits) are de-duplicated, keeping the earliest.
   - Normalization now also collapses dash variants (en/em dash etc.),
     zero-width joiners/marks, and stray whitespace, which previously
     could break the clause regex on some PDFs.
   - chunk_text() no longer emits a near-empty trailing chunk when
     `overlap` divides awkwardly into the remaining text.

Other robustness properties kept from the previous version:
- Chunks are NEVER silently dropped just because no clause ID has been
  detected yet - they are kept with clause_id=None instead of being
  discarded. Unlabeled chunks can use the validated document/section/page
  citation form; the text remains searchable and embeddable.
- CLAUSE_PATTERN-equivalent matching handles dot separators ("15.1.2")
  and Persian/Arabic-Indic digits ("۱۵-۱-۲"); digits are normalized
  before matching so mixed digit scripts don't break detection.
- extract_text_by_page() records per-page extraction errors instead of
  silently turning a failure into "".
- chunk_pdf_with_diagnostics() returns per-document diagnostics (chars
  extracted, detected clause root, which pages matched, which clause IDs
  were found per page, which were classified as appendix, which errored,
  how many chunks ended up unlabeled) so "why did this PDF only extract
  3 clauses" is answerable in one command instead of guesswork.

New in this version (from chunker_robust.py):
- retrieve_by_clause() / retrieve_clause_text() / group_chunks_by_clause()
  for pulling all chunks belonging to one clause (optionally including
  its sub-clauses) once a ChunkResult exists.
- print_clause_summary() / print_clause() / export_debug_json() for
  quick CLI inspection.
- A CLI (`python chunker.py file.pdf ...`) with flags for chunk size,
  overlap, minimum start page, an explicit --chapter-id override (skips
  auto-detection if you already know the root), --list-clauses,
  --clause <id> [--children], and --json <path> for a full debug dump.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .document_metadata import classify_document

logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class ClauseMatch:
    clause_id: str
    start: int
    end: int
    raw: str


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page_number: int
    clause_id: str | None
    source: str = ""
    document_id: str = ""
    document_title: str = ""
    document_domain: str = "regulation"
    standard_name: str = ""
    standard_version: str = ""
    document_number: str = ""
    chapter: str = ""
    section_id: str = ""
    parent_section_id: str = ""
    heading: str = ""
    chunk_index: int = 0
    section_chunk_index: int = 0
    previous_chunk_id: str = ""
    next_chunk_id: str = ""
    previous_section_chunk_id: str = ""
    next_section_chunk_id: str = ""
    content_hash: str = ""
    is_toc: bool = False
    pdf_page_number: int = 0
    printed_page_number: int = 0
    pdf_page_label: str = ""
    total_pdf_pages: int = 0
    chunk_kind: str = "text"
    table_id: str = ""
    table_row_count: int = 0
    table_column_count: int = 0
    table_data_json: str = ""


@dataclass
class PageDiagnostic:
    page_number: int
    char_count: int
    is_appendix: bool
    clause_match_found: bool
    detected_clause_ids: list[str] = field(default_factory=list)
    extraction_error: str | None = None
    is_toc: bool = False
    printed_page_number: int = 0
    pdf_page_label: str = ""
    table_count: int = 0


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    extraction_error: str | None = None
    printed_page_number: int = 0
    pdf_page_label: str = ""


@dataclass
class ExtractedTable:
    page_number: int
    table_index: int
    rows: list[list[str]]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)


@dataclass
class ChunkResult:
    chunks: list[Chunk]
    pages: list[PageDiagnostic] = field(default_factory=list)
    detected_clause_root: str | None = None

    @property
    def total_chars_extracted(self) -> int:
        return sum(p.char_count for p in self.pages)

    @property
    def pages_with_errors(self) -> list[int]:
        return [p.page_number for p in self.pages if p.extraction_error]

    @property
    def pages_with_clause_match(self) -> list[int]:
        return [p.page_number for p in self.pages if p.clause_match_found]

    @property
    def appendix_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.is_appendix]

    @property
    def unlabeled_chunk_count(self) -> int:
        return sum(1 for c in self.chunks if c.clause_id is None)

    @property
    def unique_clause_ids(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for chunk in self.chunks:
            if chunk.clause_id and chunk.clause_id not in seen:
                seen.add(chunk.clause_id)
                result.append(chunk.clause_id)
        return result

    def summary(self) -> str:
        labeled = len(self.chunks) - self.unlabeled_chunk_count
        lines = [
            f"Pages processed: {len(self.pages)}",
            f"Total chars extracted: {self.total_chars_extracted:,}",
            f"Detected clause root: {self.detected_clause_root or 'NONE (using generic fallback pattern)'}",
            f"Chunks produced: {len(self.chunks):,}",
            f"  - with a clause_id: {labeled:,}",
            f"  - unlabeled (clause_id=None): {self.unlabeled_chunk_count:,}",
            f"Unique clauses found: {len(self.unique_clause_ids):,}",
            f"Pages with extraction errors: {self.pages_with_errors or 'none'}",
            f"Pages where the clause pattern matched: {self.pages_with_clause_match or 'none'}",
            f"Pages classified as appendix: {self.appendix_pages or 'none'}",
        ]
        if self.total_chars_extracted == 0:
            lines.append(
                "-> No text extracted at all: PDF is likely scanned/image-only, "
                "encrypted, or uses a font encoding pypdf cannot decode. Needs OCR "
                "or a different library (pdfplumber / PyMuPDF)."
            )
        elif not self.pages_with_clause_match:
            lines.append(
                "-> Text was extracted but no clause numbers were detected anywhere "
                "in this document. Every chunk will have clause_id=None and will use "
                "document/section/page citations. This document may not use a dash/dot numeric clause scheme "
                "at all (e.g. 'Article 4' or lettered sections instead)."
            )
        elif self.unlabeled_chunk_count:
            lines.append(
                f"-> {self.unlabeled_chunk_count} chunk(s) have no clause_id (usually "
                f"front matter/intro text before the first numbered clause on a page). "
                f"These are kept, searchable, and cited by document/section/page."
            )
        return "\n".join(lines)


# ============================================================================
# NORMALIZATION
# ============================================================================

_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

_CHAR_TRANSLATION = str.maketrans({
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "ۀ": "ه",
    "ة": "ه",
    "ؤ": "و",
    "إ": "ا",
    "أ": "ا",
    "ٱ": "ا",
    "ـ": "",
    "\u200c": " ",  # zero-width non-joiner
    "\u200d": " ",  # zero-width joiner
    "\u200e": " ",  # left-to-right mark
    "\u200f": " ",  # right-to-left mark
})


def normalize_persian_text(text: str) -> str:
    """Normalize Arabic/Persian Unicode variants, digits, and dashes/
    whitespace so clause detection and downstream search/embedding only
    ever have to deal with one canonical form."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_CHAR_TRANSLATION)
    text = text.translate(_DIGIT_TRANSLATION)

    # Collapse the many Unicode dash variants down to a plain hyphen so the
    # clause regex doesn't need to enumerate them.
    text = re.sub(r"[‐‑‒–—−﹘﹣－]", "-", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)

    return text


_MEASUREMENT_UNIT_PATTERN = re.compile(
    r"(?:میلی\s*متر|سانتی\s*متر|متر|کیلو\s*متر|درصد|سلسیوس|سانتی\s*گراد|"
    r"درجه|ثانیه|کیلو\s*گرم|گرم|لیتر|لوکس|پاسکال|bar\b|mm\b|cm\b|m\b|%)",
    re.IGNORECASE,
)
_PERSIAN_SLASH_DECIMAL_PATTERN = re.compile(
    r"(?<![\d/])(?P<fraction>\d{1,4})\s*/\s*(?P<integer>\d{1,4})(?![\d/])"
)


def persian_decimal_aliases(text: str) -> list[dict[str, str]]:
    """Resolve RTL slash-decimals only when a nearby measurement unit confirms intent.

    In the source typography, ``38/0 متر`` represents ``0.38 متر`` and
    ``5/2 متر بر ثانیه`` represents ``2.5 متر بر ثانیه``. Dates and standard
    identifiers are intentionally excluded.
    """
    normalized_text = normalize_persian_text(text)
    aliases: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _PERSIAN_SLASH_DECIMAL_PATTERN.finditer(normalized_text):
        suffix = normalized_text[match.end():match.end() + 35]
        unit_match = _MEASUREMENT_UNIT_PATTERN.search(suffix)
        if not unit_match:
            continue
        fraction = match.group("fraction")
        integer = str(int(match.group("integer")))
        resolved = f"{integer}.{fraction}"
        unit = re.sub(r"\s+", " ", unit_match.group(0)).strip()
        raw = re.sub(r"\s+", "", match.group(0))
        key = (raw, resolved)
        if key in seen:
            continue
        seen.add(key)
        aliases.append({"source": raw, "normalized": resolved, "unit": unit})
    return aliases


# ============================================================================
# CLAUSE DETECTION
# ============================================================================

# Generic pattern used only to *detect* which root number a document uses
# (see detect_clause_root). Matches "N-N", "N-N-N", etc: 1-2 digit root,
# 1-3 digit later segments (so 4-digit years like "2023" never match a
# segment and can't get mistaken for part of a clause number).
_GENERIC_MULTISEGMENT_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})(?:\s*[-.]\s*\d{1,3}){1,4}(?!\d)"
)

# Fallback pattern used to find clause-like sequences when no root could
# be confidently detected. Looser than the anchored pattern (more
# false-positive risk), but better than extracting nothing.
_GENERIC_FALLBACK_PATTERN = re.compile(
    r"(?<!\d)\d{1,2}(?:\s*[-.]\s*\d{1,3}){1,4}(?!\d)"
)

APPENDIX_PATTERN = re.compile(r"پیوست\s*[0-9۰-۹]+", re.IGNORECASE)


def detect_clause_root(
    page_texts: list[str],
    min_occurrences: int = 3,
    min_share: float = 0.3,
) -> str | None:
    """
    Auto-detect the top-level chapter/clause number a document's internal
    references use (the "15" in "15-1-2-3" for Mabhas 15, the "4" in
    "4-2-1" for Mabhas 4, etc.) by finding the most common leading number
    across every dash/dot-separated numeric sequence in the document.

    Returns None if no number is common enough to be confident about
    (below min_occurrences absolute count, or below min_share of all
    matches) - callers should fall back to a generic, unanchored pattern
    in that case rather than guessing wrong.
    """
    roots: Counter = Counter()
    for text in page_texts:
        for m in _GENERIC_MULTISEGMENT_PATTERN.finditer(text):
            roots[m.group(1)] += 1

    if not roots:
        return None

    root, count = roots.most_common(1)[0]
    total = sum(roots.values())

    if count < min_occurrences or (count / total) < min_share:
        logger.info(
            "No confident clause root detected (best candidate %r: %d/%d matches, "
            "%.0f%% share) - falling back to generic pattern.",
            root, count, total, 100 * count / total,
        )
        return None

    logger.info(
        "Detected clause root %r (%d/%d matches, %.0f%% share).",
        root, count, total, 100 * count / total,
    )
    return root


def _compile_clause_patterns(root: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Build the standard-order and reversed/RTL-order clause regexes,
    anchored to `root`.

    Examples recognized for root="20":
        standard:  20-1, 20-1-1, 20-3-2-4, 20 - 3 - 2 - 4, 20.3.2.4
        reversed:  -1-2-4-20, - 1 - 2 - 4 - 20
    """
    chapter = re.escape(root)

    standard = re.compile(
        rf"(?<![\d-]){chapter}(?:\s*[-.]\s*\d{{1,3}}){{1,5}}\s*[-.]?(?!\d)",
        re.MULTILINE,
    )

    reversed_form = re.compile(
        rf"(?<!\d)-\s*\d{{1,3}}(?:\s*[-.]\s*\d{{1,3}}){{0,4}}\s*[-.]\s*{chapter}(?!\d)",
        re.MULTILINE,
    )

    return standard, reversed_form


def canonicalize_clause_id(raw_id: str, root: str | None = None) -> str | None:
    """Convert a raw regex match into a canonical "root-a-b-c" clause ID.

    When `root` is given, the match is anchored: the root must appear as
    either the first or last numeric segment (to handle reversed/RTL
    extraction), and the result always starts with the root. Matches that
    don't actually contain the root are rejected (returns None) rather
    than silently mis-parsed.

    When `root` is None (no confident root was detected for this
    document), any numeric segments found are just joined in the order
    they appear.
    """
    nums = re.findall(r"\d+", raw_id)
    if not nums:
        return None

    if root is None:
        if len(nums) < 2:
            return None
        return "-".join(nums)

    root = str(root)
    if nums[0] == root:
        parts = nums
    elif nums[-1] == root:
        parts = [root] + nums[:-1]
    else:
        return None

    if len(parts) < 2:
        return None

    return "-".join(parts)


def find_clause_matches(text: str, root: str | None) -> list[ClauseMatch]:
    """Find clause identifiers and their positions in `text`.

    Positions are used downstream to split text into clause-specific
    segments *before* character-count chunking, so a chunk can't
    accidentally inherit a later clause's ID just because that clause
    happened to appear later in the same 800-char window.
    """
    text = normalize_persian_text(text)
    raw_matches: list[tuple[int, int, str, str]] = []

    if root:
        standard, reversed_form = _compile_clause_patterns(root)
        for pattern in (standard, reversed_form):
            for m in pattern.finditer(text):
                cid = canonicalize_clause_id(m.group(0), root)
                if cid and not _looks_like_standard_identifier(text, m.start()):
                    raw_matches.append((m.start(), m.end(), cid, m.group(0)))
    else:
        for m in _GENERIC_FALLBACK_PATTERN.finditer(text):
            cid = canonicalize_clause_id(m.group(0), root=None)
            if cid:
                raw_matches.append((m.start(), m.end(), cid, m.group(0)))

    raw_matches.sort(key=lambda x: (x[0], x[1]))

    # De-duplicate overlapping detections (e.g. standard- and reversed-form
    # patterns both matching the same digits), keeping the earliest.
    selected: list[ClauseMatch] = []
    last_end = -1
    for start, end, cid, raw in raw_matches:
        if start < last_end:
            continue
        selected.append(ClauseMatch(clause_id=cid, start=start, end=end, raw=raw))
        last_end = end

    return selected


_STANDARD_IDENTIFIER_PREFIX = re.compile(
    r"(?:ASME|ASTM|ISO|EN|BS|DIN|CSA|ISIRI|AWWA|ASSE|MSS|JIS)\s*(?:[A-Z]\s*)?$",
    re.IGNORECASE,
)


def _looks_like_standard_identifier(text: str, start: int) -> bool:
    """Do not interpret standards such as ASME16.1 as regulation clauses."""
    if start > 0 and text[start - 1].isascii() and text[start - 1].isalpha():
        return True
    prefix = text[max(0, start - 24):start]
    if re.search(r"(?:^|[\s,(])B\s*$", prefix, re.IGNORECASE):
        return True
    match = _STANDARD_IDENTIFIER_PREFIX.search(prefix)
    return bool(match and "\n\n" not in match.group(0))


def find_clause_ids(text: str, root: str | None) -> list[str]:
    """Unique clause IDs found in `text`, in order of first appearance."""
    seen: set[str] = set()
    result: list[str] = []
    for match in find_clause_matches(text, root):
        if match.clause_id not in seen:
            seen.add(match.clause_id)
            result.append(match.clause_id)
    return result


def guess_clause_id(text: str, root: str | None = None) -> str | None:
    """Return the last clause ID found in a text segment."""
    matches = find_clause_matches(text, root)
    return matches[-1].clause_id if matches else None


# ============================================================================
# PDF EXTRACTION
# ============================================================================

def _detect_printed_page_number(raw_text: str, physical_page: int, total_pages: int) -> int:
    """Best-effort printed number from a standalone number near a page edge.

    This is deliberately separate from the physical PDF index. A missing or
    ambiguous printed number remains zero rather than being invented.
    """
    normalized = normalize_persian_text(raw_text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    edge_lines = lines[:4] + lines[-4:]
    for line in edge_lines:
        if not re.fullmatch(r"\d{1,4}", line):
            continue
        value = int(line)
        if 0 < value <= total_pages and value <= physical_page:
            return value
    return 0


def _extract_pdf_pages(pdf_path: str | Path) -> list[ExtractedPage]:
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    labels = list(reader.page_labels)
    results: list[ExtractedPage] = []

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
            error = None
        except Exception as exc:  # noqa: BLE001
            raw = ""
            error = str(exc)
            logger.warning("Page %d: text extraction failed: %s", page_number, exc)
        results.append(ExtractedPage(
            page_number=page_number,
            text=normalize_persian_text(raw),
            extraction_error=error,
            printed_page_number=_detect_printed_page_number(raw, page_number, total_pages),
            pdf_page_label=str(labels[page_number - 1]) if page_number <= len(labels) else "",
        ))
    return results


def extract_text_by_page(pdf_path: str | Path) -> list[tuple[int, str, str | None]]:
    """Returns (page_number, normalized_text, error) for every page.

    A page that fails to extract gets text="" and error=<message> instead
    of silently disappearing into an empty string with no trace of why.
    """
    return [
        (page.page_number, page.text, page.extraction_error)
        for page in _extract_pdf_pages(pdf_path)
    ]


def _logical_table_cell(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # PyMuPDF exposes Persian table cells in visual order. python-bidi is a
    # transitive dependency in the current stack; the simple reverse fallback
    # keeps table ingestion available if it is absent.
    if re.search(r"[\u0600-\u06ff]", text):
        try:
            from bidi.algorithm import get_display
            text = get_display(text)
        except ImportError:  # pragma: no cover - dependency/environment specific
            text = text[::-1]
    return normalize_persian_text(text).strip()


def extract_tables_by_page(pdf_path: str | Path) -> dict[int, list[ExtractedTable]]:
    """Extract ruled tables as cell grids while retaining plain-text fallback."""
    try:
        import fitz
    except ImportError:  # pragma: no cover - optional backwards-compatible path
        logger.warning("PyMuPDF is unavailable; continuing without table-aware chunks.")
        return {}

    document = fitz.open(str(pdf_path))
    output: dict[int, list[ExtractedTable]] = {}
    try:
        for page_index, page in enumerate(document, start=1):
            try:
                detected = page.find_tables()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Page %d: table detection failed: %s", page_index, exc)
                continue
            for table_index, table in enumerate(detected.tables):
                rows = [
                    [_logical_table_cell(cell) for cell in row]
                    for row in table.extract()
                ]
                rows = [row for row in rows if any(row)]
                nonempty = sum(bool(cell) for row in rows for cell in row)
                if len(rows) < 2 or table.col_count < 2 or nonempty < 4:
                    continue
                output.setdefault(page_index, []).append(ExtractedTable(
                    page_number=page_index,
                    table_index=table_index,
                    rows=rows,
                ))
    finally:
        document.close()
    return output


def _table_text_parts(table: ExtractedTable, chunk_size: int) -> list[str]:
    width = table.column_count
    padded = [row + [""] * (width - len(row)) for row in table.rows]
    headers = [cell or f"Column {index + 1}" for index, cell in enumerate(padded[0])]
    header_line = "Table columns: " + " | ".join(
        f"{index + 1}={header}" for index, header in enumerate(headers)
    )
    row_lines = []
    for row_index, row in enumerate(padded[1:], start=1):
        values = " | ".join(
            f"{headers[index]}={cell}" for index, cell in enumerate(row) if cell
        )
        if values:
            row_lines.append(f"Table row {row_index}: {values}")
    if not row_lines:
        row_lines = ["Table row 1: " + " | ".join(padded[0])]

    parts: list[str] = []
    current = header_line
    for line in row_lines:
        proposed = f"{current}\n{line}"
        if len(proposed) <= chunk_size or current == header_line:
            current = proposed
        else:
            parts.append(current)
            current = f"{header_line}\n{line}"
    if current:
        parts.append(current)
    return parts


def _table_section(table: ExtractedTable, segments: list[tuple[str, str]]) -> str:
    tokens = set(re.findall(r"[A-Za-z]+\d[\w.-]*|\d+[\w.-]+", " ".join(
        cell for row in table.rows for cell in row
    )))
    best_clause = ""
    best_overlap = -1
    for clause_id, segment_text in segments:
        overlap = sum(token in segment_text for token in tokens)
        if overlap > best_overlap:
            best_clause, best_overlap = clause_id, overlap
    return best_clause


def _parent_section_id(section_id: str) -> str:
    parts = section_id.split("-")
    return "-".join(parts[:-1]) if len(parts) > 2 else ""


# ============================================================================
# CHUNKING
# ============================================================================

def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 0) -> list[str]:
    """Sliding-window chunking by character count.

    Stops cleanly at the end of `text` instead of emitting a near-empty
    trailing chunk when overlap doesn't divide evenly into the remainder.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    step = chunk_size - overlap
    result: list[str] = []
    start = 0

    while start < len(text):
        piece = text[start:start + chunk_size]
        if piece.strip():
            result.append(piece.strip())

        if start + chunk_size >= len(text):
            break

        start += step

    return result


_LIST_ITEM_PATTERN = re.compile(
    r"^(?:[-•●▪]\s+|\d+\s*[-.)]\s*|[الفبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی]\s*[-–—]\s*)"
)


def _logical_blocks(text: str) -> list[str]:
    """Group wrapped PDF lines without merging distinct numbered/list items."""
    blocks: list[str] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                blocks.append(" ".join(current).strip())
                current = []
            continue
        starts_item = bool(_LIST_ITEM_PATTERN.match(line))
        if starts_item and current:
            blocks.append(" ".join(current).strip())
            current = []
        current.append(line)
    if current:
        blocks.append(" ".join(current).strip())
    return [block for block in blocks if block]


def chunk_structured_text(text: str, chunk_size: int = 1200) -> list[str]:
    """Pack paragraphs/list items without splitting a logical item when possible."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    blocks = _logical_blocks(text)
    if not blocks:
        return []
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(chunk_text(block, chunk_size=chunk_size, overlap=0))
            continue
        proposed = block if not current else f"{current}\n{block}"
        if len(proposed) <= chunk_size:
            current = proposed
        else:
            chunks.append(current)
            current = block
    if current:
        chunks.append(current)
    return chunks


def _extract_heading(segment_text: str, clause_id: str | None) -> str:
    """Keep a best-effort extracted heading; never synthesize one."""
    first_line = next((line.strip() for line in segment_text.splitlines() if line.strip()), "")
    if clause_id and first_line:
        first_line = re.sub(r"^\s*\d+(?:\s*[-.]\s*\d+)+\s*[-.]?\s*", "", first_line)
    return first_line[:200].strip()


def _looks_like_toc(
    page_text: str,
    clause_ids: list[str],
    page_number: int,
    total_pages: int,
) -> bool:
    """Detect dense heading-only contents pages conservatively."""
    if len(clause_ids) < 8:
        return False
    front_matter_limit = max(15, int(total_pages * 0.20))
    return (
        page_number <= front_matter_limit
        and len(page_text.strip()) / max(len(clause_ids), 1) < 180
    )


def _finalize_chunk_links(chunks: list[Chunk]) -> None:
    for index, chunk in enumerate(chunks):
        chunk.chunk_index = index
        chunk.previous_chunk_id = chunks[index - 1].chunk_id if index else ""
        chunk.next_chunk_id = chunks[index + 1].chunk_id if index + 1 < len(chunks) else ""
    grouped: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.section_id, []).append(chunk)
    for section_chunks in grouped.values():
        for index, chunk in enumerate(section_chunks):
            chunk.section_chunk_index = index
            chunk.previous_section_chunk_id = section_chunks[index - 1].chunk_id if index else ""
            chunk.next_section_chunk_id = section_chunks[index + 1].chunk_id if index + 1 < len(section_chunks) else ""


def _clause_segments(
    page_text: str,
    root: str | None,
    previous_clause_id: str | None,
) -> tuple[list[tuple[str, str]], str | None, list[str]]:
    """
    Split a page into (clause_id, text) segments *before* character
    chunking, e.g.:

        [15-1-1 text]
        [15-1-2 text]
        [15-1-3 text]

    This is the key accuracy fix versus splitting by raw character count
    first: each clause gets its own region, so an 800-char chunking pass
    can't accidentally straddle two clauses and mislabel the earlier
    clause's text with the later clause's ID.

    Returns (segments, new_previous_clause_id, clause_ids_found_on_page).
    """
    matches = find_clause_matches(page_text, root)

    if not matches:
        if page_text.strip():
            return [(previous_clause_id or "", page_text)], previous_clause_id, []
        return [], previous_clause_id, []

    segments: list[tuple[str, str]] = []

    # Text before the first clause on the page belongs to the previous
    # clause (usually just a repeated page header/footer or a paragraph
    # that continues from the prior page).
    prefix = page_text[:matches[0].start].strip()
    if prefix:
        segments.append((previous_clause_id or "", prefix))

    for index, match in enumerate(matches):
        next_start = (
            matches[index + 1].start if index + 1 < len(matches) else len(page_text)
        )
        body = page_text[match.start:next_start].strip()
        if body:
            segments.append((match.clause_id, body))

    return segments, matches[-1].clause_id, [m.clause_id for m in matches]


def chunk_pdf(
    pdf_path: str | Path,
    doc_id: str = "regulatory",
    source: str | None = None,
    chunk_size: int = 1200,
    overlap: int = 0,
    document_domain: str = "regulation",
    standard_name: str = "",
    standard_version: str = "",
    document_number: str = "",
) -> list[Chunk]:
    """Backwards-compatible entry point: returns just the chunk list.

    Use chunk_pdf_with_diagnostics() if you want to know *why* a PDF
    produced few/zero chunks or how many ended up unlabeled.
    """
    return chunk_pdf_with_diagnostics(
        pdf_path,
        doc_id=doc_id,
        source=source,
        chunk_size=chunk_size,
        overlap=overlap,
        document_domain=document_domain,
        standard_name=standard_name,
        standard_version=standard_version,
        document_number=document_number,
    ).chunks


def chunk_pdf_with_diagnostics(
    pdf_path: str | Path,
    doc_id: str = "regulatory",
    source: str | None = None,
    chapter_id: str | int | None = None,
    chunk_size: int = 1200,
    overlap: int = 0,
    skip_appendix: bool = True,
    min_page: int = 1,
    document_domain: str = "regulation",
    standard_name: str = "",
    standard_version: str = "",
    document_number: str = "",
) -> ChunkResult:
    """
    Extract, detect the clause root (unless `chapter_id` is given to
    override auto-detection), segment each page by clause, then chunk
    each clause segment by character count.
    """
    source = source or doc_id
    classification = classify_document(document_domain, standard_name, standard_version)
    if not document_number:
        document_match = re.match(r"^\s*(\d{1,2})(?:\D|$)", source)
        document_number = document_match.group(1) if document_match else ""

    extracted_pages = _extract_pdf_pages(pdf_path)
    tables_by_page = extract_tables_by_page(pdf_path)
    total_pdf_pages = len(extracted_pages)

    if chapter_id is not None:
        root: str | None = str(chapter_id)
    else:
        # Detect the clause root from non-appendix pages only, so tables/
        # figures in appendices (which can contain unrelated dash-separated
        # numbers) don't skew detection of the document's real clause
        # numbering scheme.
        non_appendix_texts = [
            page.text for page in extracted_pages
            if not APPENDIX_PATTERN.search(page.text[:500])
        ]
        root = detect_clause_root(non_appendix_texts)

    result_chunks: list[Chunk] = []
    diagnostics: list[PageDiagnostic] = []
    counter = 0
    current_clause_id: str | None = None

    for extracted_page in extracted_pages:
        page_number = extracted_page.page_number
        page_text = extracted_page.text
        extraction_error = extracted_page.extraction_error
        if page_number < min_page:
            continue

        is_appendix = skip_appendix and bool(APPENDIX_PATTERN.search(page_text[:500]))

        if is_appendix:
            current_clause_id = None
            diagnostics.append(PageDiagnostic(
                page_number=page_number,
                char_count=len(page_text),
                is_appendix=True,
                clause_match_found=False,
                detected_clause_ids=[],
                extraction_error=extraction_error,
                printed_page_number=extracted_page.printed_page_number,
                pdf_page_label=extracted_page.pdf_page_label,
                table_count=len(tables_by_page.get(page_number, [])),
            ))
            continue

        segments, current_clause_id, segment_clause_ids = _clause_segments(
            page_text=page_text,
            root=root,
            previous_clause_id=current_clause_id,
        )

        page_is_toc = _looks_like_toc(
            page_text,
            segment_clause_ids,
            page_number,
            total_pdf_pages,
        )

        for segment_clause_id, segment_text in segments:
            # Segments with no clause ID (front matter before the first
            # numbered clause on a page) are preserved as unlabeled chunks
            # rather than dropped - see module docstring.
            cid = segment_clause_id or None

            section_id = cid or f"page-{page_number}"
            heading = _extract_heading(segment_text, cid)
            pieces = (
                chunk_structured_text(segment_text, chunk_size=chunk_size)
                if overlap == 0
                else chunk_text(segment_text, chunk_size=chunk_size, overlap=overlap)
            )
            for piece in pieces:
                result_chunks.append(Chunk(
                    chunk_id=f"{doc_id}-p{page_number}-c{counter}",
                    text=piece,
                    page_number=page_number,
                    clause_id=cid,
                    source=source,
                    document_id=doc_id,
                    document_title=source,
                    document_domain=classification.document_domain,
                    standard_name=classification.standard_name,
                    standard_version=classification.standard_version,
                    document_number=document_number,
                    chapter=(cid.split("-")[0] if cid else (root or "")),
                    section_id=section_id,
                    parent_section_id=_parent_section_id(section_id) if cid else "",
                    heading=heading,
                    content_hash=hashlib.sha256(normalize_persian_text(piece).encode("utf-8")).hexdigest(),
                    is_toc=page_is_toc,
                    pdf_page_number=page_number,
                    printed_page_number=extracted_page.printed_page_number,
                    pdf_page_label=extracted_page.pdf_page_label,
                    total_pdf_pages=total_pdf_pages,
                ))
                counter += 1

        for table in tables_by_page.get(page_number, []):
            table_clause_id = _table_section(table, segments) or current_clause_id or ""
            table_section_id = table_clause_id or f"page-{page_number}"
            table_id = f"{doc_id}-p{page_number}-table{table.table_index}"
            table_json = json.dumps(table.rows, ensure_ascii=False, separators=(",", ":"))
            table_heading = next((cell for cell in table.rows[0] if cell), "Extracted table")
            for table_part_index, table_text in enumerate(_table_text_parts(table, chunk_size)):
                result_chunks.append(Chunk(
                    chunk_id=f"{table_id}-part{table_part_index}",
                    text=table_text,
                    page_number=page_number,
                    clause_id=table_clause_id or None,
                    source=source,
                    document_id=doc_id,
                    document_title=source,
                    document_domain=classification.document_domain,
                    standard_name=classification.standard_name,
                    standard_version=classification.standard_version,
                    document_number=document_number,
                    chapter=(table_clause_id.split("-")[0] if table_clause_id else (root or "")),
                    section_id=table_section_id,
                    parent_section_id=(
                        _parent_section_id(table_section_id) if table_clause_id else ""
                    ),
                    heading=table_heading[:200],
                    content_hash=hashlib.sha256(
                        normalize_persian_text(table_text).encode("utf-8")
                    ).hexdigest(),
                    is_toc=page_is_toc,
                    pdf_page_number=page_number,
                    printed_page_number=extracted_page.printed_page_number,
                    pdf_page_label=extracted_page.pdf_page_label,
                    total_pdf_pages=total_pdf_pages,
                    chunk_kind="table",
                    table_id=table_id,
                    table_row_count=table.row_count,
                    table_column_count=table.column_count,
                    table_data_json=table_json,
                ))
                counter += 1

        diagnostics.append(PageDiagnostic(
            page_number=page_number,
            char_count=len(page_text),
            is_appendix=False,
            clause_match_found=bool(segment_clause_ids),
            detected_clause_ids=segment_clause_ids,
            extraction_error=extraction_error,
            is_toc=page_is_toc,
            printed_page_number=extracted_page.printed_page_number,
            pdf_page_label=extracted_page.pdf_page_label,
            table_count=len(tables_by_page.get(page_number, [])),
        ))

    _finalize_chunk_links(result_chunks)
    return ChunkResult(chunks=result_chunks, pages=diagnostics, detected_clause_root=root)


# ============================================================================
# RETRIEVAL
# ============================================================================

def group_chunks_by_clause(result: ChunkResult) -> dict[str, list[Chunk]]:
    grouped: dict[str, list[Chunk]] = {}
    for chunk in result.chunks:
        if chunk.clause_id:
            grouped.setdefault(chunk.clause_id, []).append(chunk)
    return grouped


def retrieve_by_clause(
    result: ChunkResult,
    clause_id: str,
    include_children: bool = False,
) -> list[Chunk]:
    """Fetch every chunk belonging to `clause_id` (and optionally its
    sub-clauses, e.g. "15-1" also matching "15-1-2")."""
    normalized = normalize_persian_text(clause_id)
    target = canonicalize_clause_id(normalized, result.detected_clause_root)

    if not target:
        raise ValueError(f"Invalid clause ID: {clause_id}")

    if include_children:
        prefix = target + "-"
        return [
            c for c in result.chunks
            if c.clause_id == target
            or (c.clause_id is not None and c.clause_id.startswith(prefix))
        ]

    return [c for c in result.chunks if c.clause_id == target]


def retrieve_clause_text(
    result: ChunkResult,
    clause_id: str,
    include_children: bool = False,
) -> str:
    chunks = retrieve_by_clause(result, clause_id, include_children=include_children)
    return "\n\n".join(c.text for c in chunks)


# ============================================================================
# REPORTING / DEBUG
# ============================================================================

def print_clause_summary(result: ChunkResult) -> None:
    print("\n=== CHUNKER SUMMARY ===")
    print(result.summary())

    grouped = group_chunks_by_clause(result)

    print("\n=== CLAUSES ===")
    for clause_id in result.unique_clause_ids:
        chunks = grouped.get(clause_id, [])
        pages = sorted({c.page_number for c in chunks})
        print(f"{clause_id:15} | {len(chunks):4} chunks | pages: {pages}")


def print_clause(result: ChunkResult, clause_id: str, include_children: bool = False) -> None:
    chunks = retrieve_by_clause(result, clause_id, include_children=include_children)

    print(f"\n=== CLAUSE {clause_id}{' + CHILDREN' if include_children else ''} ===")
    print(f"Retrieved chunks: {len(chunks)}")

    if not chunks:
        print("No chunks found.")
        return

    for i, chunk in enumerate(chunks, start=1):
        print(f"\n--- {i}/{len(chunks)} | {chunk.chunk_id} | page {chunk.page_number} | clause {chunk.clause_id} ---")
        print(chunk.text)


def export_debug_json(result: ChunkResult, output_path: str | Path) -> None:
    payload = {
        "summary": result.summary(),
        "detected_clause_root": result.detected_clause_root,
        "clauses": result.unique_clause_ids,
        "chunks": [asdict(c) for c in result.chunks],
        "pages": [asdict(p) for p in result.pages],
    }
    Path(output_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Regulatory PDF chunker with auto-detected clause roots.")
    parser.add_argument("pdf", help="Path to PDF")
    parser.add_argument("--doc-id", default=None, help="Defaults to the PDF filename stem.")
    parser.add_argument("--source", default=None, help="Defaults to --doc-id.")
    parser.add_argument(
        "--chapter-id",
        default=None,
        help="Override auto-detection and force this clause root (e.g. '15').",
    )
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=0)
    parser.add_argument("--min-page", type=int, default=1, help="Skip PDF pages before this page (cover/TOC).")
    parser.add_argument("--list-clauses", action="store_true")
    parser.add_argument("--clause", help="Retrieve one exact clause, e.g. 15-1-2")
    parser.add_argument("--children", action="store_true", help="Include sub-clauses with --clause.")
    parser.add_argument("--json", help="Export full debug JSON to this path.")

    args = parser.parse_args()
    doc_id = args.doc_id or Path(args.pdf).stem

    result = chunk_pdf_with_diagnostics(
        pdf_path=args.pdf,
        doc_id=doc_id,
        source=args.source,
        chapter_id=args.chapter_id,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        min_page=args.min_page,
    )

    print_clause_summary(result)

    if args.list_clauses:
        print("\n=== CLAUSE IDS ===")
        for clause_id in result.unique_clause_ids:
            print(clause_id)

    if args.clause:
        print_clause(result, args.clause, include_children=args.children)

    if args.json:
        export_debug_json(result, args.json)
        print(f"\nDebug JSON written to: {args.json}")


if __name__ == "__main__":
    main()
