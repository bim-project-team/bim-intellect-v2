## Setup
```bash
git clone https://github.com/bim-project-team/bim-intellect-v2.git
cd bim-intellect-v2
docker-compose up -d                          # starts Neo4j
pip install -r requirements.txt
uvicorn main:app --reload
```

## Regulation RAG v2

Build the versioned multilingual index before using regulation chat:

```bash
python -m rag.indexer --source-dir dataset/sources --rebuild
python -m rag.indexer --status
```

The Chat tab preserves a bounded conversation by `conversation_id` and offers an optional **Use Stronger Models** toggle. Configuration is documented in `.env.example`; the full ingestion, retrieval, memory, routing, citation, and validation design is in [docs/RAG_ARCHITECTURE.md](docs/RAG_ARCHITECTURE.md).

## Multi-file projects

The RAG Corpus tab accepts multiple PDFs per upload and lists documents actually stored in Chroma. The Pipeline tab accepts multiple IFC files, groups them under an explicit project/building ID, lists per-file ingestion state, and runs provenance-aware intra-file and cross-file clash checks only after their coordinate frames are verified. API contracts and federation safety rules are documented in [docs/MULTI_FILE_WORKFLOWS.md](docs/MULTI_FILE_WORKFLOWS.md).
