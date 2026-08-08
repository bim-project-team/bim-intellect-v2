"""
Chunks a regulatory PDF into overlapping text segments for embedding.
Each chunk retains a traceable clause reference for citation integrity.

MVP scope: single regulatory PDF (Mabhas 15 — elevator/stair clearance).
"""
import re
import unicodedata
from dataclasses import dataclass
from pypdf import PdfReader


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page_number: int
    clause_id: str | None

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
    })

    return text.translate(replacements)

CLAUSE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2}(?:\s*-\s*\d{1,2}){2,4})(?!\d)"
)
def canonicalize_clause_id(raw_id: str) -> str:
    """Convert RTL-reversed clause IDs to canonical order."""
    parts = re.findall(r"\d+", raw_id)
    return "-".join(parts)
def extract_text_by_page(pdf_path: str) -> list[tuple[int, str]]:
    reader = PdfReader(pdf_path)
    return [
        (i + 1, normalize_persian_text(page.extract_text() or ""))
        for i, page in enumerate(reader.pages)
    ]


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


def chunk_pdf(pdf_path: str, doc_id: str = "mabhas15") -> list[Chunk]:
    pages = extract_text_by_page(pdf_path)
    result = []
    counter = 0

    for page_num, page_text in pages:
        current_clause_id: str | None = None

        for piece in chunk_text(page_text):
            if not piece.strip():
                continue

            detected_clause_id = guess_clause_id(piece)

            if detected_clause_id is not None:
                current_clause_id = detected_clause_id

            result.append(
                Chunk(
                    chunk_id=f"{doc_id}-p{page_num}-c{counter}",
                    text=piece.strip(),
                    page_number=page_num,
                    clause_id=current_clause_id,
                )
            )
            counter += 1

    return result

if __name__ == "__main__":
    import sys, json
    chunks = chunk_pdf(sys.argv[1])
    print(json.dumps([c.__dict__ for c in chunks[:5]], indent=2, ensure_ascii=False))
    print(f"Total chunks: {len(chunks)}")
