"""
Robust Persian/Arabic regulatory PDF chunker.

Key design:
1. Normalize Persian/Arabic Unicode and digits.
2. Detect clause headings in LTR and RTL-extracted forms.
3. Split each page into clause segments BEFORE character chunking.
4. Chunk inside each clause segment, so one chunk cannot accidentally inherit
   a later clause that happened to appear later in the same 800-char window.
5. Preserve page + clause provenance.
6. Provide diagnostics and exact clause retrieval.

Examples recognized:
    20-1
    20-1-1
    20-3-2-4
    20 - 3 - 2 - 4
    20.3.2.4
    -1-2-4-20
    - 1 - 2 - 4 - 20
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

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


@dataclass
class PageDiagnostic:
    page_number: int
    char_count: int
    is_appendix: bool
    clause_match_found: bool
    detected_clause_ids: list[str] = field(default_factory=list)
    extraction_error: str | None = None


@dataclass
class ChunkResult:
    chunks: list[Chunk]
    pages: list[PageDiagnostic] = field(default_factory=list)

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
    def unique_clause_ids(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for chunk in self.chunks:
            if chunk.clause_id and chunk.clause_id not in seen:
                seen.add(chunk.clause_id)
                result.append(chunk.clause_id)
        return result

    def summary(self) -> str:
        lines = [
            f"Pages processed: {len(self.pages)}",
            f"Total chars extracted: {self.total_chars_extracted:,}",
            f"Chunks produced: {len(self.chunks):,}",
            f"Chunks with clause_id: {sum(bool(c.clause_id) for c in self.chunks):,}",
            f"Chunks with clause_id=None: {sum(c.clause_id is None for c in self.chunks):,}",
            f"Unique clauses: {len(self.unique_clause_ids):,}",
            f"Pages with extraction errors: {self.pages_with_errors or 'none'}",
            f"Pages with clause matches: {self.pages_with_clause_match or 'none'}",
            f"Appendix pages: {self.appendix_pages or 'none'}",
        ]

        if self.total_chars_extracted == 0:
            lines.append(
                "WARNING: no text extracted. The PDF may be scanned/image-only, "
                "encrypted, or use an encoding pypdf cannot decode."
            )
        elif not self.pages_with_clause_match:
            lines.append(
                "WARNING: text was extracted but no clause headings were detected."
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
    "\u200c": " ",
    "\u200d": " ",
    "\u200e": " ",
    "\u200f": " ",
})


def normalize_persian_text(text: str) -> str:
    """Normalize Unicode variants, Persian/Arabic digits and dash variants."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_CHAR_TRANSLATION)
    text = text.translate(_DIGIT_TRANSLATION)

    text = re.sub(r"[‐‑‒–—−﹘﹣－]", "-", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)

    return text


# ============================================================================
# CLAUSE DETECTION
# ============================================================================

def _compile_clause_patterns(
    chapter_id: str | int,
) -> tuple[re.Pattern[str], re.Pattern[str]]:
    chapter = re.escape(str(chapter_id))

    # Chapter first:
    #   20-3-2-4-
    #   20 - 3 - 2 - 4
    #   20.3.2.4
    standard = re.compile(
        rf"(?<![\d-])"
        rf"{chapter}"
        rf"(?:\s*[-.]\s*\d{{1,3}}){{1,5}}"
        rf"\s*[-.]?"
        rf"(?!\d)",
        re.MULTILINE,
    )

    # RTL/reversed:
    #   -3-2-4-20
    #   - 3 - 2 - 4 - 20
    reversed_form = re.compile(
        rf"(?<!\d)"
        rf"-\s*\d{{1,3}}"
        rf"(?:\s*[-.]\s*\d{{1,3}}){{0,4}}"
        rf"\s*[-.]\s*{chapter}"
        rf"(?!\d)",
        re.MULTILINE,
    )

    return standard, reversed_form


def canonicalize_clause_id(raw_id: str, chapter_id: str | int) -> str | None:
    nums = re.findall(r"\d+", raw_id)
    chapter = str(chapter_id)

    if not nums:
        return None

    if nums[0] == chapter:
        parts = nums
    elif nums[-1] == chapter:
        parts = [chapter] + nums[:-1]
    else:
        return None

    # A clause must have at least chapter + one section component.
    if len(parts) < 2:
        return None

    return "-".join(parts)


def find_clause_matches(
    text: str,
    chapter_id: str | int = 20,
) -> list[ClauseMatch]:
    """
    Find clause identifiers and positions.

    This function detects the number itself. Later logic uses the positions
    to split text into clause-specific regions.
    """
    text = normalize_persian_text(text)
    standard, reversed_form = _compile_clause_patterns(chapter_id)

    raw_matches: list[tuple[int, int, str, str]] = []

    for match in standard.finditer(text):
        cid = canonicalize_clause_id(match.group(0), chapter_id)
        if cid:
            raw_matches.append(
                (match.start(), match.end(), cid, match.group(0))
            )

    for match in reversed_form.finditer(text):
        cid = canonicalize_clause_id(match.group(0), chapter_id)
        if cid:
            raw_matches.append(
                (match.start(), match.end(), cid, match.group(0))
            )

    raw_matches.sort(key=lambda x: (x[0], x[1]))

    # De-duplicate overlapping detections.
    selected: list[ClauseMatch] = []
    last_end = -1

    for start, end, cid, raw in raw_matches:
        if start < last_end:
            continue

        selected.append(
            ClauseMatch(
                clause_id=cid,
                start=start,
                end=end,
                raw=raw,
            )
        )
        last_end = end

    return selected


def find_clause_ids(
    text: str,
    chapter_id: str | int = 20,
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for match in find_clause_matches(text, chapter_id=chapter_id):
        if match.clause_id not in seen:
            seen.add(match.clause_id)
            result.append(match.clause_id)

    return result


def guess_clause_id(
    text: str,
    chapter_id: str | int = 20,
) -> str | None:
    matches = find_clause_matches(text, chapter_id=chapter_id)
    return matches[-1].clause_id if matches else None


# ============================================================================
# PDF EXTRACTION
# ============================================================================

APPENDIX_PATTERN = re.compile(r"پیوست\s*[0-9]+", re.IGNORECASE)


def extract_text_by_page(
    pdf_path: str | Path,
) -> list[tuple[int, str, str | None]]:
    reader = PdfReader(str(pdf_path))
    results: list[tuple[int, str, str | None]] = []

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
            error = None
        except Exception as exc:  # noqa: BLE001
            raw = ""
            error = str(exc)
            logger.warning("Page %d extraction failed: %s", page_number, exc)

        results.append(
            (page_number, normalize_persian_text(raw), error)
        )

    return results


# ============================================================================
# CHUNKING
# ============================================================================

def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 150,
) -> list[str]:
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


def _clause_segments(
    page_text: str,
    chapter_id: str | int,
    previous_clause_id: str | None,
) -> tuple[list[tuple[str, str]], str | None, list[str]]:
    """
    Split a page into (clause_id, text) segments.

    Example:
        [20-3-2-1 text]
        [20-3-2-2 text]
        [20-3-2-3 text]

    This is the key fix: each clause gets its own region before 800-char
    chunking occurs.
    """
    matches = find_clause_matches(page_text, chapter_id=chapter_id)

    if not matches:
        if page_text.strip():
            return [(previous_clause_id or "", page_text)], previous_clause_id, []
        return [], previous_clause_id, []

    segments: list[tuple[str, str]] = []

    # Text before the first clause belongs to the previous clause, usually
    # just a repeated page header.
    prefix = page_text[:matches[0].start].strip()
    if prefix:
        cid = previous_clause_id or ""
        segments.append((cid, prefix))

    for index, match in enumerate(matches):
        next_start = (
            matches[index + 1].start
            if index + 1 < len(matches)
            else len(page_text)
        )

        body = page_text[match.start:next_start].strip()
        if body:
            segments.append((match.clause_id, body))

    return segments, matches[-1].clause_id, [m.clause_id for m in matches]


def chunk_pdf_with_diagnostics(
    pdf_path: str | Path,
    doc_id: str = "regulatory",
    chapter_id: str | int = 20,
    chunk_size: int = 800,
    overlap: int = 150,
    skip_appendix: bool = True,
    min_page: int = 1,
) -> ChunkResult:
    pages = extract_text_by_page(pdf_path)

    result_chunks: list[Chunk] = []
    diagnostics: list[PageDiagnostic] = []
    counter = 0
    current_clause_id: str | None = None

    for page_number, page_text, extraction_error in pages:
        if page_number < min_page:
            continue

        is_appendix = (
            skip_appendix
            and bool(APPENDIX_PATTERN.search(page_text[:500]))
        )

        detected_ids = find_clause_ids(
            page_text,
            chapter_id=chapter_id,
        )

        if is_appendix:
            current_clause_id = None
            diagnostics.append(
                PageDiagnostic(
                    page_number=page_number,
                    char_count=len(page_text),
                    is_appendix=True,
                    clause_match_found=bool(detected_ids),
                    detected_clause_ids=detected_ids,
                    extraction_error=extraction_error,
                )
            )
            continue

        segments, current_clause_id, segment_clause_ids = _clause_segments(
            page_text=page_text,
            chapter_id=chapter_id,
            previous_clause_id=current_clause_id,
        )

        for segment_clause_id, segment_text in segments:
            # Prefixes with no clause ID are preserved as unlabeled chunks.
            cid = segment_clause_id or None

            for piece in chunk_text(
                segment_text,
                chunk_size=chunk_size,
                overlap=overlap,
            ):
                result_chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}-p{page_number}-c{counter}",
                        text=piece,
                        page_number=page_number,
                        clause_id=cid,
                    )
                )
                counter += 1

        diagnostics.append(
            PageDiagnostic(
                page_number=page_number,
                char_count=len(page_text),
                is_appendix=False,
                clause_match_found=bool(segment_clause_ids),
                detected_clause_ids=segment_clause_ids,
                extraction_error=extraction_error,
            )
        )

    return ChunkResult(
        chunks=result_chunks,
        pages=diagnostics,
    )


def chunk_pdf(
    pdf_path: str | Path,
    doc_id: str = "regulatory",
    chapter_id: str | int = 20,
    chunk_size: int = 800,
    overlap: int = 150,
) -> list[Chunk]:
    return chunk_pdf_with_diagnostics(
        pdf_path,
        doc_id=doc_id,
        chapter_id=chapter_id,
        chunk_size=chunk_size,
        overlap=overlap,
    ).chunks


# ============================================================================
# RETRIEVAL
# ============================================================================

def get_clause_ids(result: ChunkResult) -> list[str]:
    return result.unique_clause_ids


def group_chunks_by_clause(
    result: ChunkResult,
) -> dict[str, list[Chunk]]:
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
    target = canonicalize_clause_id(
        normalize_persian_text(clause_id),
        chapter_id=(re.findall(r"\d+", normalize_persian_text(clause_id)) or ["20"])[0],
    )

    if not target:
        raise ValueError(f"Invalid clause ID: {clause_id}")

    if include_children:
        prefix = target + "-"
        return [
            c for c in result.chunks
            if c.clause_id == target
            or (
                c.clause_id is not None
                and c.clause_id.startswith(prefix)
            )
        ]

    return [
        c for c in result.chunks
        if c.clause_id == target
    ]


def retrieve_clause_text(
    result: ChunkResult,
    clause_id: str,
    include_children: bool = False,
) -> str:
    chunks = retrieve_by_clause(
        result,
        clause_id,
        include_children=include_children,
    )
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
        print(
            f"{clause_id:15} | "
            f"{len(chunks):4} chunks | "
            f"pages: {pages}"
        )


def print_clause(
    result: ChunkResult,
    clause_id: str,
    include_children: bool = False,
) -> None:
    chunks = retrieve_by_clause(
        result,
        clause_id,
        include_children=include_children,
    )

    print(
        f"\n=== CLAUSE {clause_id}"
        f"{' + CHILDREN' if include_children else ''} ==="
    )
    print(f"Retrieved chunks: {len(chunks)}")

    if not chunks:
        print("No chunks found.")
        return

    for i, chunk in enumerate(chunks, start=1):
        print(
            f"\n--- {i}/{len(chunks)} | {chunk.chunk_id} | "
            f"page {chunk.page_number} | clause {chunk.clause_id} ---"
        )
        print(chunk.text)


def export_debug_json(
    result: ChunkResult,
    output_path: str | Path,
) -> None:
    payload = {
        "summary": result.summary(),
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
    parser = argparse.ArgumentParser(
        description="Robust Persian regulatory PDF chunker."
    )

    parser.add_argument("pdf", help="Path to PDF")
    parser.add_argument("--chapter-id", default="20")
    parser.add_argument("--doc-id", default="regulatory")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument(
        "--min-page",
        type=int,
        default=1,
        help="Skip PDF pages before this page (useful for cover/TOC).",
    )
    parser.add_argument("--list-clauses", action="store_true")
    parser.add_argument("--clause", help="Retrieve exact clause, e.g. 20-3-2-4")
    parser.add_argument("--children", action="store_true")
    parser.add_argument("--json", help="Export debug JSON")

    args = parser.parse_args()

    result = chunk_pdf_with_diagnostics(
        pdf_path=args.pdf,
        doc_id=args.doc_id,
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
        print_clause(
            result,
            args.clause,
            include_children=args.children,
        )

    if args.json:
        export_debug_json(result, args.json)
        print(f"\nDebug JSON written to: {args.json}")


if __name__ == "__main__":
    main()
