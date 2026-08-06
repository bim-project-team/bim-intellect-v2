# BIM-Intellect — System Documentation

> **Hybrid RAG for Building Regulatory Compliance**  
> Combines vector retrieval from building regulations (Mabhas 15) with graph retrieval from IFC building models to answer compliance questions with citations to both legal clauses and specific building elements.

---

## Table of Contents

1. [Goal & Problem Statement](#1-goal--problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Flows](#3-data-flows)
4. [Module Reference](#4-module-reference)
5. [Database Schemas](#5-database-schemas)
6. [API Endpoints](#6-api-endpoints)
7. [Configuration](#7-configuration)
8. [Deployment](#8-deployment)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Goal & Problem Statement

### The Problem
Building compliance checking is currently a **manual, error-prone process**:
- Engineers must cross-reference IFC models (thousands of elements) against hundreds of pages of regulations.
- Clash detection tools find geometric conflicts but cannot explain *why* they violate a code.
- Regulation documents (e.g., Saudi Mabhas 15) are dense, clause-numbered PDFs that are hard to query.

### The Solution
BIM-Intellect is a **Hybrid RAG system** that:
1. **Ingests** building IFC models into a Neo4j graph (elements, spaces, geometries, clashes).
2. **Ingests** regulation PDFs into a ChromaDB vector store (chunked, embedded, clause-tracked).
3. **Answers** natural language questions by routing to the correct data source(s) and synthesizing a cited response.

### Example Questions the System Answers
| Question | Sources Used | Answer Contains |
|----------|-------------|-----------------|
| "What is the minimum width for an elevator door per Mabhas 15?" | Vector only | [Clause 4.2] with exact requirement |
| "How many stairs are on the ground floor?" | Graph only | Element count + IDs + names |
| "Does this building comply with Mabhas 15 for elevator clearances?" | **Both** | Regulation requirement + building element list + gap analysis |
| "Which elements clash with the elevator shaft?" | Graph only | Clashing element names, types, overlap volumes |
| "List all zero-gap clearance violations on Level 5." | Graph only | Specific elements, relationship metrics |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │    Chat     │  │  Pipeline   │  │ RAG Corpus  │  │    Results      │   │
│  │  (Hybrid)   │  │(IFC→Neo4j) │  │(PDF→Chroma) │  │(Clash/Violation)│   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘   │
│         │                │                │                   │           │
│         └────────────────┴────────────────┴───────────────────┘           │
│                                    │                                        │
│                              /api/* endpoints                             │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────┼────────────────────────────────────────┐
│                              API LAYER                                       │
│                              api/routes.py                                   │
│  • Routes HTTP requests to the correct backend module                       │
│  • Lazy-loads RAG modules with sys.path injection for absolute imports      │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
           ┌─────────────────────────┼─────────────────────────┐
           │                         │                         │
           ▼                         ▼                         ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│      RAG LAYER       │  │     GRAPH LAYER      │  │    IFC LAYER        │
│       rag/           │  │     bim_graph/       │  │   extract_graph.py  │
│                      │  │                      │  │                     │
│  chunker.py          │  │  neo4j_client.py     │  │  Parses .ifc        │
│  embedder.py         │  │  load_to_neo4j.py    │  │  → nodes.csv        │
│  retriever.py        │  │  clash_pipeline.py   │  │  → edges.csv        │
│  orchestrator.py     │  │  cypher_generator.py │  │                     │
│  prompts.py          │  │  graph_retriever.py  │  │                     │
│  openrouter_client.py│  │  config.py           │  │                     │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
           │                         │                         │
           ▼                         ▼                         ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   ChromaDB (local)   │  │   Neo4j (Docker)     │  │   CSV Files         │
│   ./chroma_db        │  │   bolt://localhost   │  │   nodes.csv         │
│   Collection:        │  │   :7687              │  │   edges.csv         │
│   "regulations"      │  │   APOC plugin        │  │                     │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
           │                         │
           └──────────┬──────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LLM LAYER (OpenRouter)                              │
│  • Routing decisions (vector vs graph vs both)                              │
│  • Cypher query generation from natural language                              │
│  • Final answer synthesis with citations                                      │
│  Model: openai/gpt-4o-mini (chat), openai/text-embedding-3-small (embed)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Flows

### Flow A: IFC Model → Neo4j Graph

```
dataset/210_King_Merged.ifc
    │
    ▼
extract_graph.py  ──►  nodes.csv  +  edges.csv
    │                         │              │
    │    ┌────────────────────┘              │
    │    │                                   │
    │    ▼                                   ▼
    │  Element nodes                      Relationships
    │  • id (GUID)                        • AGGREGATES
    │  • ifcType                          • CONTAINS
    │  • name                             • BOUNDS
    │  • storeyId / storeyName            • PORT_OF
    │  • minX, minY, minZ                 • CLASHES_WITH (added later)
    │  • maxX, maxY, maxZ
    │
    ▼
bim_graph/load_to_neo4j.py
    │
    ▼  (batched UNWIND Cypher, MERGE-based, idempotent)
    ▼
Neo4j (:Element) nodes with dynamic labels (:IfcWall, :IfcSlab, etc.)
    │
    ▼
bim_graph/clash_pipeline.py
    │
    ▼  (AABB overlap + clearance detection)
    ▼
Neo4j (:Element)-[:CLASHES_WITH {issue, metric}]->(:Element)
```

**Key design decisions:**
- Uses `apoc.create.addLabels` so each `IfcWall` node is also `:IfcWall` — enables `MATCH (w:IfcWall)`.
- Uses `MERGE` so re-loading the same CSV is safe (idempotent).
- Clash relationships carry `issue: 'CLASH' | 'CLEARANCE_VIOLATION'` and `metric: float` (overlap volume or gap distance).

### Flow B: Regulation PDF → ChromaDB

```
dataset/sources/mabhas-15-elvators-stairs.pdf
    │
    ▼
rag/chunker.py
    │
    ▼  (sliding window: 800 chars, 150 overlap)
    ▼  (regex extracts clause numbers like 4.2, 5.1.3)
    ▼
list[Chunk]
  • chunk_id:  "mabhas15-p3-c12"
  • text:      "...the minimum clear width shall be 1.2m..."
  • page:      3
  • clause_id: "4.2"
    │
    ▼
rag/embedder.py
    │
    ▼  (OpenRouter text-embedding-3-small, batched 96 per request)
    ▼
ChromaDB PersistentCollection "regulations"
  • ids:        [chunk_id]
  • documents:  [chunk text]
  • metadata:   {page_number, clause_id}
```

### Flow C: User Question → Hybrid Answer

```
User: "Does Level 5 comply with Mabhas 15 stair requirements?"
    │
    ▼
rag/orchestrator.py :: route()
    │
    ▼  (LLM with ROUTER_PROMPT decides data sources)
    ▼
┌─────────────────┐    ┌─────────────────┐
│ needs_vector?   │    │ needs_graph?    │
│      YES        │    │      YES        │
└────────┬────────┘    └────────┬────────┘
         │                      │
         ▼                      ▼
rag/embedder.py          bim_graph/graph_retriever.py
query_similar()          ask()
    │                         │
    ▼                         ▼
Chroma chunks            Cypher → Neo4j → records
[Clause 4.2] text        Element names, clash metrics
    │                         │
    └──────────┬──────────────┘
               ▼
    rag/orchestrator.py :: generate()
    │
    ▼  (COMBINE_PROMPT → single LLM call)
    ▼
GPT-4o-mini synthesizes:
  "Level 5 has 2 stairs (IfcStair id=...). 
   Mabhas 15 [Clause 4.2] requires a minimum width of 1.2m. 
   The main stair measures 1.5m — COMPLIANT."
    │
    ▼
Frontend renders:
  • Chat bubble with answer
  • Source tags: [Clause 4.2] | IfcStair Main Stair
  • Sidebar: "Sources consulted: Regulations, Building Graph"
```

---

## 4. Module Reference

### 4.1 IFC Extraction

| File | Responsibility |
|------|----------------|
| **`extract_graph.py`** | Parses `.ifc` via IfcOpenShell. Extracts every element's bounding box, IFC type, name, and storey into `nodes.csv`. Extracts spatial relationships (AGGREGATES, CONTAINS, BOUNDS, PORT_OF) into `edges.csv`. |
| **`nodes.csv`** | One row per element: `id, type, name, storey_id, storey_name, min_x, min_y, min_z, max_x, max_y, max_z`. |
| **`edges.csv`** | One row per relationship: `source_id, target_id, rel_type`. |

### 4.2 Graph Layer (`bim_graph/`)

| File | Responsibility |
|------|----------------|
| **`config.py`** | Centralized constants: Neo4j URI, credentials, CSV paths, batch sizes, default IFC path. |
| **`neo4j_client.py`** | Thin wrapper around `neo4j.GraphDatabase.driver`. Provides `run()`, `run_batched()`, and context-manager lifecycle. |
| **`load_to_neo4j.py`** | Bulk-loads CSVs into Neo4j using batched `UNWIND` + `MERGE`. Uses `apoc.create.addLabels` for dynamic IFC-type labels. Creates uniqueness constraint on `Element.id`. |
| **`clash_pipeline.py`** | AABB clash detection and clearance violation detection. Queries Neo4j for element geometries, computes overlaps/gaps, writes results back as `:CLASHES_WITH` relationships with `issue` and `metric` properties. |
| **`diagnose_load.py`** | Debugging utility to identify dangling edge rows (source/target IDs not found in nodes). |
| **`cypher_generator.py`** | NL → Cypher via LLM. Prompt includes full schema. Safety rails block destructive keywords (`DROP`, `DELETE`, `CREATE`, `MERGE`) and reject non-read queries. |
| **`graph_retriever.py`** | End-to-end graph RAG: calls `cypher_generator`, executes via `neo4j_client`, formats records into LLM-readable context. Handles both Neo4j driver objects and plain dicts safely. |

### 4.3 RAG Layer (`rag/`)

| File | Responsibility |
|------|----------------|
| **`openrouter_client.py`** | Shared OpenRouter client (OpenAI SDK pointed at `https://openrouter.ai/api/v1`). Lazy client construction, retry logic with exponential backoff + jitter, error classification (`LLMConfigError` vs `LLMRequestError`). |
| **`chunker.py`** | PDF → overlapping text chunks. Extracts page numbers, guesses clause IDs via regex `\d+\.\d+(\.\d+)?`. Output: `list[Chunk]` with `chunk_id`, `text`, `page_number`, `clause_id`. |
| **`embedder.py`** | Embeds chunks via OpenRouter (`text-embedding-3-small`) and stores in ChromaDB persistent collection. Batches requests (default 96). Custom `EmbeddingFunction` for Chroma compatibility. |
| **`retriever.py`** | Legacy vector-only RAG. Queries Chroma, builds citation-tagged context (`[Clause X.X] text`), calls GPT-4o-mini with strict citation prompt. Includes `ChatSession` for multi-turn conversations with fresh retrieval each turn. |
| **`prompts.py`** | Three system prompts: `ROUTER_PROMPT` (decides vector/graph/both), `CYPHER_GENERATOR_PROMPT` (schema-aware NL→Cypher), `COMBINE_PROMPT` (synthesizes both sources into one answer). |
| **`orchestrator.py`** | **The brain.** Three-step pipeline: (1) `route()` — LLM decides sources; (2) `retrieve()` — fetches from Chroma and/or Neo4j; (3) `generate()` — single LLM call with combined context. Falls back to both sources if routing fails. |

### 4.4 API Layer (`api/`)

| File | Responsibility |
|------|----------------|
| **`routes.py`** | All FastAPI endpoints. Lazy-loads RAG modules with `sys.path` injection for absolute imports. Routes: `/extract`, `/load`, `/ingest`, `/analyze`, `/clashes`, `/violations`, `/issues`, `/ask`, `/ask-vector`, `/ask-graph`, `/rag/upload`, `/rag/ingest`, `/rag/status`, `/rag/clear`. |

### 4.5 Frontend (`templates/` + `static/`)

| File | Responsibility |
|------|----------------|
| **`index.html`** | Four-tab SPA: Chat (hybrid Q&A), Pipeline (IFC ingest + console), RAG Corpus (PDF drag-drop upload + status), Results (clash/violation tables). |
| **`style.css`** | Monochrome black & white theme. Georgia serif headings, Courier monospace data/console. Responsive chat layout with sidebar on desktop. |
| **`app.js`** | All frontend logic: tab navigation, chat bubbles with typing indicators, example questions, drag-and-drop PDF upload, corpus status polling, pipeline console logging, results table rendering. |

### 4.6 Infrastructure

| File | Responsibility |
|------|----------------|
| **`main.py`** | FastAPI bootstrap. Mounts API router, serves static files and Jinja2 templates. |
| **`docker-compose.yml`** | Neo4j with APOC plugin. Persists graph data to Docker volume. |
| **`requirements.txt`** | All Python dependencies. |
| **`env.example`** | Template for environment variables (`OPENROUTER_API_KEY`, `NEO4J_URI`, etc.). |

---

## 5. Database Schemas

### 5.1 Neo4j Graph Schema

**Nodes:**
```cypher
(:Element {
  id: string,           // Generated GUID (not raw IFC ID)
  ifcType: string,      // e.g., "IfcWall", "IfcSlab"
  name: string,         // Full IFC name, e.g., "Floor:BLDG1-FLR-Generic:339256"
  storeyId: string,
  storeyName: string,   // e.g., "BLDG. 1,2,3- LEVEL 5 FLR. FIN."
  minX, minY, minZ: float,
  maxX, maxY, maxZ: float
})
// Plus dynamic label matching ifcType: :IfcWall, :IfcSlab, etc.
```

**Relationships:**
```cypher
(:Element)-[:AGGREGATES]->(:Element)
(:Element)-[:CONTAINS]->(:Element)
(:Element)-[:BOUNDS]->(:Element)
(:Element)-[:PORT_OF]->(:Element)
(:Element)-[:CLASHES_WITH {issue: 'CLASH', metric: float}]->(:Element)
(:Element)-[:CLASHES_WITH {issue: 'CLEARANCE_VIOLATION', metric: float}]->(:Element)
```

**Constraints:**
```cypher
CREATE CONSTRAINT element_id IF NOT EXISTS
FOR (e:Element) REQUIRE e.id IS UNIQUE
```

### 5.2 ChromaDB Schema

**Collection:** `regulations`

| Field | Type | Description |
|-------|------|-------------|
| `ids` | string[] | `mabhas15-p3-c12` (doc_id + page + counter) |
| `documents` | string[] | Chunk text (800 chars, 150 overlap) |
| `metadatas` | dict[] | `{page_number: int, clause_id: string}` |

**Embedding function:** OpenRouter `text-embedding-3-small` via custom `OpenRouterEmbeddingFunction`.

---

## 6. API Endpoints

### 6.1 IFC / Graph Pipeline

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/extract` | `POST` | Parse IFC → `nodes.csv` + `edges.csv`. Query params: `ifc_path`, `storey[]`, `type[]`. |
| `/api/load` | `POST` | Load CSVs into Neo4j. Query param: `reset` (bool). |
| `/api/ingest` | `POST` | `/extract` + `/load` in one call. |
| `/api/analyze` | `POST` | Run clash & clearance detection on loaded graph. |
| `/api/clashes` | `GET` | Return hard clashes (overlap). |
| `/api/violations` | `GET` | Return clearance violations (too close). |
| `/api/issues` | `GET` | Return both clashes and violations. |

### 6.2 RAG Q&A

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ask` | `POST` | **Hybrid RAG.** Auto-routes to vector/graph/both. Body: `{question}`. |
| `/api/ask-vector` | `POST` | Debug: regulation vector search only. |
| `/api/ask-graph` | `POST` | Debug: graph query only. |

### 6.3 RAG Corpus Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/rag/upload` | `POST` | Upload PDF via multipart/form-data. Chunk + embed + store. Form: `file`, Query: `doc_id`. |
| `/api/rag/ingest` | `POST` | Ingest PDF from disk path. Query: `pdf_path`, `doc_id`. |
| `/api/rag/status` | `GET` | Chroma collection stats (count, sample IDs). |
| `/api/rag/clear` | `DELETE` | Drop the entire Chroma collection. **Destructive.** |

---

## 7. Configuration

### 7.1 Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | **Required.** API key from openrouter.ai |
| `OPENROUTER_CHAT_MODEL` | `openai/gpt-4o-mini` | LLM for chat/routing/cypher |
| `OPENROUTER_EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model |
| `OPENROUTER_MAX_RETRIES` | `3` | Retry count for transient failures |
| `OPENROUTER_BASE_BACKOFF` | `1.5` | Base seconds for exponential backoff |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Local ChromaDB storage path |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt URL |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password` | Neo4j password |

### 7.2 Key Constants

| Constant | Location | Value | Purpose |
|----------|----------|-------|---------|
| `BATCH_SIZE` | `bim_graph/config.py` | 500 | Neo4j UNWIND batch size |
| `EMBED_BATCH_SIZE` | `rag/embedder.py` | 96 | OpenRouter embedding batch size |
| `CHUNK_SIZE` | `rag/chunker.py` | 800 | Characters per text chunk |
| `CHUNK_OVERLAP` | `rag/chunker.py` | 150 | Character overlap between chunks |
| `n_results` | `rag/orchestrator.py` | 5 | Chroma top-k retrieval |

---

## 8. Deployment

### 8.1 Prerequisites

```bash
# Python 3.12+
pip install -r requirements.txt

# Neo4j with APOC (via Docker)
docker-compose up -d

# Environment
cp env.example .env
# Edit .env with your OPENROUTER_API_KEY
```

### 8.2 First-Time Setup

```bash
# 1. Start Neo4j
docker-compose up -d

# 2. Ingest regulations (CLI)
python -m rag.embedder dataset/sources/mabhas-15-elvators-stairs.pdf

# 3. Extract IFC → CSV
python extract_graph.py

# 4. Load into Neo4j
python -m bim_graph.load_to_neo4j --reset

# 5. Run clash detection
python -m bim_graph.clash_pipeline

# 6. Start API
python main.py
# → http://localhost:8000
```

### 8.3 Using the Web UI

1. Open `http://localhost:8000`
2. **RAG Corpus** tab → drag & drop `mabhas-15-elvators-stairs.pdf` → Upload
3. **Pipeline** tab → click "Run Ingestion & Clash Detection"
4. **Chat** tab → ask: *"How many clearance violations exist on Level 5?"*

---

## 9. Troubleshooting

### 9.1 "RAG chunker/embedder modules not available" (501)

**Cause:** `rag/` uses absolute imports (`from chunker import ...`) that fail when imported from `api/routes.py`.  
**Fix:** `routes.py` dynamically adds `rag/` to `sys.path` before lazy imports. If this still fails, ensure `rag/__init__.py` exists.

### 9.2 Cypher queries return empty results

**Cause:** The LLM generates queries with exact storey name matches (`= 'Level 5'`) instead of `CONTAINS`.  
**Fix:** The `CYPHER_GENERATOR_PROMPT` explicitly instructs `CONTAINS` for storey names and `ENDS WITH` for numeric IDs embedded in element names.

### 9.3 `KeyError: 'issue'` in graph retrieval

**Cause:** `record.data()` converts Neo4j `Relationship` objects differently across driver versions.  
**Fix:** `graph_retriever.py` uses safe type detection (`_is_neo4j_relationship`) and dict fallback to handle both driver objects and plain dicts.

### 9.4 No clashes detected after `/analyze`

**Cause:** Elements may have been filtered out during `/ingest` (storey/type filter too restrictive).  
**Fix:** Run `/ingest` with no filters, or check `diagnose_load.py` for dangling edges.

### 9.5 Chroma collection empty after upload

**Cause:** `embed_and_store()` may have failed silently, or the collection path is wrong.  
**Fix:** Check `/api/rag/status` for document count. Check server logs for `LLMRequestError` during embedding.

---

## 10. Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Single source of truth** | `openrouter_client.py` is the only module that knows how to talk to OpenRouter. |
| **Idempotent ingestion** | Neo4j loads use `MERGE`; Chroma `add` with deterministic IDs is safe to re-run. |
| **Fail fast, fail loud** | `LLMConfigError` and `LLMRequestError` propagate up instead of returning empty results silently. |
| **Lazy loading** | RAG modules are imported on first endpoint hit, not at server startup — API starts even if modules are missing. |
| **Schema-aware generation** | Cypher prompt includes exact Neo4j schema (dynamic labels, relationship properties) to reduce hallucination. |
| **Safety first** | Cypher generator blocks all destructive keywords and rejects non-read query starters. |

---

*Document version: 1.0*  
*Generated: 2026-08-05*
