"""
Chunks a regulatory PDF into overlapping text segments for embedding.
Each chunk retains a traceable clause reference for citation integrity.

MVP scope: single regulatory PDF (Mabhas 15 — elevator/stair clearance).
"""
import re
from dataclasses import dataclass
from pypdf import PdfReader


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page_number: int
    clause_id: str | None


CLAUSE_PATTERN = re.compile(r"(\d+\.\d+(\.\d+)?)")


def extract_text_by_page(pdf_path: str) -> list[tuple[int, str]]:
    reader = PdfReader(pdf_path)
    return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]


def guess_clause_id(text: str) -> str | None:
    """Best-effort extraction of a clause number from chunk text for citation."""
    match = CLAUSE_PATTERN.search(text)
    return match.group(1) if match else None


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Sliding-window chunking by character count."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def chunk_pdf(pdf_path: str, doc_id: str = "mabhas15") -> list[Chunk]:
    pages = extract_text_by_page(pdf_path)
    result = []
    counter = 0
    for page_num, page_text in pages:
        for piece in chunk_text(page_text):
            if not piece.strip():
                continue
            clause = guess_clause_id(piece)
            result.append(Chunk(
                chunk_id=f"{doc_id}-p{page_num}-c{counter}",
                text=piece.strip(),
                page_number=page_num,
                clause_id=clause,
            ))
            counter += 1
    return result


if __name__ == "__main__":
    import sys, json
    chunks = chunk_pdf(sys.argv[1])
    print(json.dumps([c.__dict__ for c in chunks[:5]], indent=2, ensure_ascii=False))
    print(f"Total chunks: {len(chunks)}")
