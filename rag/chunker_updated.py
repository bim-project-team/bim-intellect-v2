"""
Chunks a regulatory PDF into overlapping text segments for embedding.
Each chunk retains a traceable clause reference for citation integrity.

MVP scope: single regulatory PDF (Mabhas 15 - elevator/stair clearance).

Robustness changes vs. original:
- Chunks are NEVER silently dropped just because no clause ID has been
  detected yet. They are kept with clause_id=None instead. This was the
  main cause of chunk_pdf() returning 0 chunks for some PDFs: any text
  before the first regex match (or an entire doc if the regex never
  matched at all) used to be discarded with no warning.
- CLAUSE_PATTERN now also matches dot separators ("15.1.2") and Persian/
  Arabic-Indic digits ("۱۵-۱-۲"), and digits are normalized before
  matching so mixed digit scripts don't break detection.
- extract_text_by_page() no longer swallows per-page extraction errors
  silently - a failing page is recorded, not just turned into "".
- chunk_pdf() returns a ChunkResult with diagnostics (per-page char
  counts, which pages matched the clause pattern, which pages were
  classified as appendix, which pages raised extraction errors) so you
  can see *why* a given PDF produced few/zero chunks instead of guessing.
- A PDF with real extractable text will now essentially always produce
  chunks (with clause_id=None where no clause number could be found)
  rather than silently returning [].
"""
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pypdf import PdfReader

logger = logging.getLogger(__name__)


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

    def summary(self) -> str:
        lines = [
            f"Pages processed: {len(self.pages)}",
            f"Total chars extracted: {self.total_chars_extracted}",
            f"Chunks produced: {len(self.chunks)}",
            f"Chunks with a clause_id: {sum(1 for c in self.chunks if c.clause_id)}",
            f"Chunks with clause_id=None: {sum(1 for c in self.chunks if c.clause_id is None)}",
            f"Pages with extraction errors: {self.pages_with_errors or 'none'}",
            f"Pages where CLAUSE_PATTERN matched: {self.pages_with_clause_match or 'none'}",
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
                "-> Text was extracted but CLAUSE_PATTERN never matched on any page. "
                "All chunks will have clause_id=None. Check raw page text for how "
                "clause numbers actually appear in this document."
            )
        return "\n".join(lines)


def normalize_persian_text(text: str) -> str:
    """Normalize Arabic/Persian Unicode variants for search and embeddings."""
    text = unicodedata.normalize("NFKC", text)

    replacements = str.maketrans({
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "ـ": "",
        # Persian/Arabic-Indic digits -> ASCII digits, so CLAUSE_PATTERN
        # and any downstream numeric parsing only need to handle one script.
        "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
        "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    })

    return text.translate(replacements)


# Accepts "15-1-2", "15.1.2", "15 - 1 - 2" (dashes or dots as separators),
# after digit normalization has already converted Persian/Arabic digits.
CLAUSE_PATTERN = re.compile(
    r"(?<!\d)(15(?:\s*[-.]\s*\d{1,2}){1,4})(?!\d)"
)

APPENDIX_PATTERN = re.compile(
    r"پیوست\s*[0-9۰-۹]+"
)


def canonicalize_clause_id(raw_id: str) -> str:
    """Convert RTL-reversed clause IDs to canonical order."""
    parts = re.findall(r"\d+", raw_id)
    return "-".join(parts)


def extract_text_by_page(pdf_path: str) -> list[tuple[int, str, str | None]]:
    """Returns (page_number, normalized_text, error) for every page.

    A page that fails to extract gets text="" and error=<message> instead
    of silently disappearing into an empty string with no trace of why.
    """
    reader = PdfReader(pdf_path)
    results = []
    for i, page in enumerate(reader.pages):
        try:
            raw = page.extract_text() or ""
            err = None
        except Exception as exc:
            raw = ""
            err = str(exc)
            logger.warning("Page %d: text extraction failed: %s", i + 1, exc)
        results.append((i + 1, normalize_persian_text(raw), err))
    return results


def guess_clause_id(text: str) -> str | None:
    """Return the last clause ID found in a text segment."""
    matches = list(CLAUSE_PATTERN.finditer(text))

    if not matches:
        return None

    raw_id = matches[-1].group(1)
    return canonicalize_clause_id(raw_id)


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Sliding-window chunking by character count."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks



def get_clause_ids(result: ChunkResult) -> list[str]:
    """Return unique clause IDs in document order."""
    seen = set()
    ids = []
    for chunk in result.chunks:
        if chunk.clause_id and chunk.clause_id not in seen:
            seen.add(chunk.clause_id)
            ids.append(chunk.clause_id)
    return ids


def retrieve_by_clause(result: ChunkResult, clause_id: str, include_children: bool = False) -> list[Chunk]:
    """Retrieve chunks for a clause; optionally include nested child clauses."""
    target = canonicalize_clause_id(normalize_persian_text(clause_id))
    return [c for c in result.chunks if c.clause_id == target or (include_children and c.clause_id and c.clause_id.startswith(target + "-"))]


def group_chunks_by_clause(result: ChunkResult) -> dict[str, list[Chunk]]:
    """Group labeled chunks by clause."""
    grouped: dict[str, list[Chunk]] = {}
    for chunk in result.chunks:
        if chunk.clause_id:
            grouped.setdefault(chunk.clause_id, []).append(chunk)
    return grouped


def print_clause_summary(result: ChunkResult) -> None:
    grouped = group_chunks_by_clause(result)
    ids = get_clause_ids(result)
    print("\n=== CHUNKER SUMMARY ===")
    print(result.summary())
    print(f"Unique clauses: {len(ids)}")
    print(f"Unlabeled chunks: {sum(c.clause_id is None for c in result.chunks)}")
    print("\n=== CLAUSES ===")
    for cid in ids:
        chunks = grouped[cid]
        pages = sorted({c.page_number for c in chunks})
        print(f"{cid:15} | {len(chunks):4} chunks | pages: {pages}")


def print_clause(result: ChunkResult, clause_id: str, include_children: bool = False) -> None:
    chunks = retrieve_by_clause(result, clause_id, include_children)
    if not chunks:
        print(f"\nNo chunks found for clause: {clause_id}")
        return
    print(f"\n=== CLAUSE {clause_id}{' + CHILDREN' if include_children else ''} ===")
    print(f"Retrieved chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- {i}/{len(chunks)} | {chunk.chunk_id} | page {chunk.page_number} | clause {chunk.clause_id} ---")
        print(chunk.text)


def interactive_clause_search(result: ChunkResult) -> None:
    ids = get_clause_ids(result)
    print("\n=== INTERACTIVE CLAUSE RETRIEVAL ===")
    print("Type a clause such as 15-1-2, '+15-1-2' for children, 'list', or 'q'.")
    while True:
        query = input("\nClause> ").strip()
        if query.lower() in {"q", "quit", "exit"}:
            break
        if query.lower() == "list":
            print("\n".join(ids))
            continue
        if not query:
            continue
        children = query.startswith("+")
        print_clause(result, query[1:] if children else query, children)

def chunk_pdf(pdf_path: str, doc_id: str = "mabhas15") -> list[Chunk]:
    """Backwards-compatible entry point: returns just the chunk list.

    Use chunk_pdf_with_diagnostics() if you want to know *why* a PDF
    produced few or zero chunks.
    """
    return chunk_pdf_with_diagnostics(pdf_path, doc_id=doc_id).chunks


def chunk_pdf_with_diagnostics(pdf_path: str, doc_id: str = "mabhas15") -> ChunkResult:
    pages = extract_text_by_page(pdf_path)
    result_chunks: list[Chunk] = []
    page_diags: list[PageDiagnostic] = []
    counter = 0
    current_clause_id: str | None = None

    for page_num, page_text, err in pages:
        is_appendix = bool(APPENDIX_PATTERN.search(page_text[:500]))
        clause_match_found = False

        if is_appendix:
            current_clause_id = None
            page_diags.append(PageDiagnostic(
                page_number=page_num,
                char_count=len(page_text),
                is_appendix=True,
                clause_match_found=False,
                extraction_error=err,
            ))
            continue

        for piece in chunk_text(page_text):
            if not piece.strip():
                continue

            detected_clause_id = guess_clause_id(piece)

            if detected_clause_id is not None:
                current_clause_id = detected_clause_id
                clause_match_found = True

            # NOTE: previously chunks were dropped here if
            # current_clause_id was still None (e.g. nothing matched yet,
            # or CLAUSE_PATTERN never matches this document at all). That
            # silently produced 0 chunks for entire PDFs. Now we keep the
            # chunk with clause_id=None instead, so text is never lost -
            # it's just unlabeled until/unless a clause number is found.
            result_chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}-p{page_num}-c{counter}",
                    text=piece.strip(),
                    page_number=page_num,
                    clause_id=current_clause_id,
                )
            )
            counter += 1

        page_diags.append(PageDiagnostic(
            page_number=page_num,
            char_count=len(page_text),
            is_appendix=False,
            clause_match_found=clause_match_found,
            extraction_error=err,
        ))

    return ChunkResult(chunks=result_chunks, pages=page_diags)



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test chunking and retrieve regulatory text by clause.")
    parser.add_argument("pdf", help="Path to PDF")
    parser.add_argument("--doc-id", default="mabhas15")
    parser.add_argument("--clause", help="Retrieve clause, e.g. 15-1-2")
    parser.add_argument("--children", action="store_true", help="Include nested clauses")
    parser.add_argument("--list-clauses", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    result = chunk_pdf_with_diagnostics(args.pdf, doc_id=args.doc_id)
    print_clause_summary(result)
    if args.list_clauses:
        print("\n=== CLAUSE LIST ===")
        print("\n".join(get_clause_ids(result)))
    if args.clause:
        print_clause(result, args.clause, args.children)
    if args.interactive:
        interactive_clause_search(result)
