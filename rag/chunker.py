import re
from typing import List, Dict, Any
from pypdf import PdfReader

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150

def chunk_pdf(pdf_path: str, doc_id: str = "doc") -> List[Dict[str, Any]]:
    """
    Reads a PDF, extracts text page by page, and splits it into overlapping chunks.
    Attempts to extract structural clause numbers (e.g., "4.2.1") via regex.
    """
    chunks = []
    
    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        print(f"Error reading PDF {pdf_path}: {e}")
        return chunks

    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if not text:
            continue
            
        # Clean up whitespace and line breaks
        text = " ".join(text.split())
        
        # Simple sliding window chunker
        start = 0
        text_len = len(text)
        
        while start < text_len:
            end = start + CHUNK_SIZE
            chunk_text = text[start:end]
            
            # Very basic regex to find clause numbers like "15-4-2" or "4.2.1"
            # Expanded to catch more generic numbers at the start of sentences
            clause_match = re.search(r'\b(?:clause|section)?\s*(\d+[\.\-]\d+(?:[\.\-]\d+)?)\b', chunk_text, re.IGNORECASE)
            clause_id = clause_match.group(1) if clause_match else "unknown"
            
            # If we couldn't find a clause but the chunk is from a regulation, label it as such
            if clause_id == "unknown":
                clause_id = f"Page {page_num}"
                
            chunk_id = f"{doc_id}-p{page_num}-c{start}"
            
            chunks.append({
                "chunk_id": chunk_id,
                "text": chunk_text.strip(),
                "page_number": page_num,
                "clause_id": clause_id
            })
            
            start += (CHUNK_SIZE - CHUNK_OVERLAP)
            
    return chunks