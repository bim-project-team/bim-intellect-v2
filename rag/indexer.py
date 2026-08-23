"""Reproducible PDF corpus indexing CLI for the regulation RAG system."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .chunker import chunk_pdf_with_diagnostics
from .config import SETTINGS, RAGSettings
from .embedder import collection_status, embed_and_store, get_client_db

logger = logging.getLogger("bim_intellect.rag.indexer")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_doc_id(path: Path, file_hash: str) -> str:
    # Hash suffix prevents same-stem collisions while keeping IDs readable.
    return f"{path.stem[:60]}-{file_hash[:12]}"


def rebuild_collection(settings: RAGSettings = SETTINGS) -> None:
    client = get_client_db(settings)
    names = {collection.name for collection in client.list_collections()}
    if settings.collection_name in names:
        client.delete_collection(settings.collection_name)
        logger.info("Deleted versioned collection '%s'", settings.collection_name)


def build_index(
    source_dir: str | Path,
    *,
    rebuild: bool = False,
    chunk_size: int = 1200,
    settings: RAGSettings = SETTINGS,
    manifest_path: str | Path | None = None,
) -> dict:
    source_dir = Path(source_dir)
    if not source_dir.exists():
        raise FileNotFoundError(f"Regulation source directory not found: {source_dir}")
    pdfs = sorted(source_dir.rglob("*.pdf"), key=lambda value: str(value).casefold())
    if not pdfs:
        raise ValueError(f"No PDF files found under {source_dir}")
    if rebuild:
        rebuild_collection(settings)

    seen_hashes: dict[str, str] = {}
    manifest: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir.resolve()),
        "collection": settings.collection_name,
        "index_version": settings.index_version,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "chunk_size": chunk_size,
        "documents": [],
        "duplicate_files": [],
        "chunk_count": 0,
    }

    for pdf_path in pdfs:
        file_hash = _file_hash(pdf_path)
        if file_hash in seen_hashes:
            manifest["duplicate_files"].append({
                "path": str(pdf_path),
                "duplicate_of": seen_hashes[file_hash],
                "sha256": file_hash,
            })
            logger.warning("Skipping byte-identical PDF %s", pdf_path.name)
            continue
        seen_hashes[file_hash] = str(pdf_path)
        doc_id = _safe_doc_id(pdf_path, file_hash)
        filename_chapter = re.match(r"^\s*(\d{1,2})(?:\D|$)", pdf_path.name)
        result = chunk_pdf_with_diagnostics(
            pdf_path,
            doc_id=doc_id,
            source=pdf_path.name,
            chunk_size=chunk_size,
            overlap=0,
        )
        # Some Persian PDF fonts extract the visible chapter number with an
        # incorrect glyph mapping (the Mabhas 12 corpus yields root "01").
        # The leading number in the source filename is authoritative document
        # metadata. Preserve segmentation from the extracted root, then map only
        # that root segment for truthful user-facing clause citations.
        canonical_chapter = filename_chapter.group(1) if filename_chapter else None
        extracted_root = result.detected_clause_root
        if canonical_chapter and extracted_root and canonical_chapter != extracted_root:
            logger.warning(
                "Mapping extracted clause root %s to filename chapter %s for %s",
                extracted_root, canonical_chapter, pdf_path.name,
            )
            for chunk in result.chunks:
                if chunk.clause_id and chunk.clause_id.startswith(f"{extracted_root}-"):
                    chunk.clause_id = f"{canonical_chapter}-{chunk.clause_id[len(extracted_root) + 1:]}"
                    chunk.section_id = chunk.clause_id
                    chunk.chapter = canonical_chapter
        indexed = embed_and_store(result.chunks, settings=settings)
        manifest["chunk_count"] += indexed
        manifest["documents"].append({
            "path": str(pdf_path),
            "filename": pdf_path.name,
            "document_id": doc_id,
            "sha256": file_hash,
            "chunks": indexed,
            "pages": len(result.pages),
            "detected_clause_root": result.detected_clause_root,
            "canonical_chapter": canonical_chapter,
            "unlabeled_chunks": result.unlabeled_chunk_count,
            "toc_chunks": sum(chunk.is_toc for chunk in result.chunks),
            "extraction_error_pages": result.pages_with_errors,
        })
        logger.info("Indexed %s: %d chunks", pdf_path.name, indexed)

    manifest["status"] = collection_status(settings)
    target = Path(manifest_path or source_dir / "rag_index_manifest.json")
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(target.resolve())
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the versioned multilingual regulation index")
    parser.add_argument("--source-dir", default="dataset/sources")
    parser.add_argument("--rebuild", action="store_true", help="Delete only the configured RAG collection first")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--manifest")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(collection_status(), ensure_ascii=False, indent=2))
        return
    manifest = build_index(
        args.source_dir,
        rebuild=args.rebuild,
        chunk_size=args.chunk_size,
        manifest_path=args.manifest,
    )
    print(json.dumps({
        "documents": len(manifest["documents"]),
        "duplicates_skipped": len(manifest["duplicate_files"]),
        "chunks": manifest["chunk_count"],
        "collection": manifest["collection"],
        "manifest": manifest["manifest_path"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
