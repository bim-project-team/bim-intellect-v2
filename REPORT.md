# BIM-Intellect v2 — Code Audit Report

Findings from a full read of the backend (Python), frontend (JS/CSS/HTML), configuration,
and a live inspection of the runtime environment (imports, tests, ChromaDB contents, venv).

**Nothing in this report has been fixed.** It is a backlog. The UI/UX rework delivered
alongside it is presentation-only and does not touch any of the logic described here.

Audited against branch `feature/ui-ux` at commit `c081492`. An earlier draft of this
report was written against an older tree; every item below has been **re-verified against
the current code**, and the ones already resolved upstream are recorded in the last
section rather than left in the backlog.

Legend: `P0` = breaks the product now · `P1` = security or correctness · `P2` = quality

---

## Verified environment state

| Check | Result |
|---|---|
| `import main` + `GET /` | works (HTTP 200) |
| `GET /static/{style.css,i18n.js,app.js}` | 200, correct sizes |
| `pytest rag/test_routing.py` | **cannot collect** — `ModuleNotFoundError: sklearn` (see P0-3) |
| Neo4j | not running — no listener on 7687, `docker` binary absent |
| ChromaDB (`rag/chroma_db`) | 120 chunks, 81 distinct clause IDs, pages 9–60, zero `unknown` |
| `venv/` | **broken** — `venv/bin/python` resolves to system `/usr/bin/python3.12`, `venv/bin/pip` is not executable, created under another user's path (`/home/rash/…`) |
| `tests/` directory | contains only `__pycache__`, and is listed in `.gitignore` |

---

## P0 — Blocks work today

### [ ] 1. The test suite cannot run: `scikit-learn` is not installed
`rag/reranker.py:10` imports `TfidfVectorizer`, so collecting `rag/test_routing.py` fails
before a single test executes. `requirements.txt` does list `scikit-learn>=1.4`, so this is
an environment gap rather than a missing declaration — but it means the one automated
check in the repo is currently dead. Fix: rebuild the virtualenv (see #2) and install from
`requirements.txt`.

### [ ] 2. The bundled virtualenv is unusable
`venv/pyvenv.cfg` records `command = /usr/bin/python3 -m venv /home/rash/Code/PROJECTS/…`,
`venv/bin/python` is a dangling link to the system interpreter, and `venv/bin/pip` is not
executable. Everything currently runs only because `site-packages` happens to be
importable via `PYTHONPATH`. Fix: delete and recreate the venv; it should not be committed
or shipped at all.

---

## P1 — Security

### [ ] 3. The Cypher safety blocklist both over-blocks and under-blocks
`bim_graph/cypher_generator.py:38-41` still filters by naive substring match on the
uppercased query.

Over-blocks (verified) — legitimate read queries rejected because they contain `SET`:
```
MATCH (e:Element) WHERE e.name CONTAINS 'Offset Wall' RETURN e LIMIT 50
MATCH (e:Element) WHERE e.name CONTAINS 'Asset 12'    RETURN e LIMIT 50
MATCH (e:Element) RETURN e.name AS dataset LIMIT 50
```
Under-blocks (verified) — these pass the filter:
```
CALL apoc.load.json('file:///etc/passwd') YIELD value RETURN value LIMIT 1
CALL apoc.cypher.runWrite('MATCH (n) DETACH DEL' + 'ETE n', {}) YIELD value RETURN value
```
`docker-compose.yml:17` sets `dbms_security_procedures_unrestricted=apoc.*`, so the first
is a genuine local-file read and the second reaches a full graph wipe via string
concatenation the blocklist cannot see — both driven by user question text.

Fix: stop filtering strings. Run generated Cypher in a read-only session
(`driver.session(default_access_mode=neo4j.READ_ACCESS)`) or under a read-only Neo4j user,
and restrict the allowed procedure set. `bim_graph/neo4j_client.py` opens every session in
the default read-write mode today (lines 34, 52, 81).

### [ ] 4. No authentication, with a permissive CORS policy
`main.py:20-26` sets `allow_origins=["*"]` together with `allow_credentials=True` — an
invalid pairing that browsers reject — and no endpoint requires a credential. An
unauthenticated caller can wipe the graph (`reset_all`), delete the vector collection
(`DELETE /api/rag/clear`), spend OpenRouter credits (`POST /api/ask`), and write files into
the project directory (`POST /api/ifc/upload`).

Fix: bind to localhost during development; add an auth dependency and an explicit origin
allow-list before exposing it.

### [ ] 5. Cypher assembled by string interpolation
`bim_graph/cypher_templates.py:37` interpolates the extracted element ID straight into the
query, and `bim_graph/neo4j_client.py:69` interpolates a label into `MATCH (n:{label})`.
The `\d{5,}` regex and the `:(Ifc\w+)` extraction make neither exploitable as written, but
it is the wrong foundation. Fix: use query parameters (`$tag`); map labels through an
allow-list.

---

## P1 — Correctness

### [ ] 6. Persian-digit citations fail validation and discard correct answers
`rag/orchestrator.py:95-98` (`CITATION_PATTERN`) matches ASCII digits only.

Verified: `[Clause 15-2-1-11, Page ۲۱]` yields an empty cited-set.

Because a regulatory citation is required whenever vector context is present, a **correct**
answer that renders its page number in Persian numerals is rejected and replaced with
"insufficient evidence". The corpus and prompts are Persian-first, so this fires in normal
use. Fix: normalise digits before matching — `rag/chunker.py` already has the translation
table.

### [ ] 7. Clash detection silently skips elements with no storey
`bim_graph/clash_pipeline.py` groups elements by storey; pandas `groupby` drops rows with a
null key by default.

Verified: on a 3-row frame with two null storeys, only 1 row was visited.

Elements whose storey lookup failed are therefore excluded from clash detection with no
warning. Fix: pass `dropna=False` and report the unassigned bucket, or fail loudly when it
is non-empty.

### [ ] 8. Generic clause fallback matches dates as clause IDs
With no detected clause root, `rag/chunker.py`'s fallback pattern turns the Persian date
`1400-03-12` into clause ID `03-12`.

Verified via `find_clause_matches("قرارداد 1400-03-12 و شماره 2-3", root=None)`.

A fabricated clause ID becomes *citable*, defeating the integrity checks upstream. Fix:
require a plausible root, or mark fallback-derived IDs as non-citable.

### [ ] 9. Two divergent retrieval implementations
`rag/retriever.py` and `rag/orchestrator.py` each carry their own validity predicate,
context builder, and prompt, and they disagree — the retriever accepts the literal string
`"none"` as a clause ID; the orchestrator rejects it. `/ask-vector` and `/ask` can give
different answers over the same corpus. Fix: one shared module.

### [ ] 10. Unit ambiguity in the clearance threshold
`bim_graph/clash_pipeline.py:18` documents `CLEARANCE_THRESHOLD = 0.25` as "feet, ~3
inches". The results column is now labelled "model units", which is honest but unresolved:
a compliance tool should know which unit its threshold is in. Fix: derive the threshold
from the model's declared unit, or assert the expected unit at ingest.

---

## P2 — Architecture and operations

### [ ] 11. Minute-long jobs run inside HTTP request handlers
The extraction / ingest / analyse routes are synchronous handlers. IFC parsing spawns
`cpu_count()` geometry workers and clash detection is a pure-Python O(n²) pass per storey.
There is no timeout, progress reporting, or cancellation, and a client disconnect does not
stop the work. `api/routes.py` now serialises them behind `_PIPELINE_LOCK`, which prevents
concurrent corruption but also means a second caller simply blocks. Fix: a task queue with
status polling.

### [ ] 12. A new Neo4j driver is created per request
`Neo4jClient.__init__` builds a fresh `GraphDatabase.driver`, and handlers instantiate one
per call, discarding the driver's connection pool each time. Fix: one module-level driver;
sessions per request.

### [ ] 13. Broad `except Exception` handlers return empty success responses
`/clashes`, `/violations`, `/issues` catch everything, print to stderr, and return `[]`
with HTTP 200. A Neo4j outage is indistinguishable from "no clashes found". Fix: return 503
with a diagnostic.

### [ ] 14. Comments reference files that do not exist
- `CYPHER_ACCURACY_ROADMAP.md` — cited by `bim_graph/graph_retriever.py:11` and
  `bim_graph/migrate_add_tags.py:9`
- `detect_clashes.py` — cited by `bim_graph/clash_pipeline.py:5` as the source of the
  clearance-threshold rationale
- `tests/test_graph_retrieval_regression.py` — `bim_graph/cypher_templates.py:11` declares
  a regression test **mandatory** for every template; neither existing template has one

### [ ] 15. `tests/` is gitignored and empty
`.gitignore` excludes `tests/`, and the only test lives at `rag/test_routing.py`. The ignore
rule hides the suite from collaborators. `.gitignore` also excludes `*.ini`, which will
silently swallow any future `pytest.ini` / `setup.cfg`. Fix: un-ignore both, move the suite
into `tests/`, and add the regression tests the code claims to require.

### [ ] 16. Committed artefacts and a misspelled module
- `extract_sotreys_type.py` — typo in a filename the API imports
- `__pycache__/`, `.pytest_cache/`, and `venv/` present in the working tree
- `artifacts/` contains committed model weights and CSV outputs (`graph_autoencoder.pt`,
  `bim_graph_dataset.pt`, extracted node/edge CSVs) — build outputs in version control

---

## Already fixed upstream (verified, no action needed)

These appeared in the first draft of this report and are resolved in the current tree:

| Was | Now |
|---|---|
| `CHROMA_PERSIST_DIR` pointed at a nonexistent path, so retrieval silently returned nothing | `rag/config.py` centralises settings and calls `load_dotenv()`; `CHROMA_DIR` comes from `RAGSettings` |
| `bim_graph/config.py` never loaded `.env`, so Neo4j credentials depended on import order | `rag/config.py:10` loads it; settings are read through a dataclass |
| `requirements.txt` claimed the RAG module was "not wired up" and commented out `chromadb`/`pypdf`/`openai` | All present and pinned, plus `sentence-transformers`, `scikit-learn`, `torch` |
| `embed_and_store` used `collection.add`, silently dropping duplicate IDs while reporting success | `rag/embedder.py:179` uses `collection.upsert` and returns the real count |
| Unvalidated `ifc_path` / `pdf_path` allowed arbitrary file reads; upload used the raw client filename | `safe_id()` for project IDs, `Path(upload.filename).name` for filenames, uploads confined to a per-project directory |
| Results table interpolated Neo4j names into `innerHTML` unescaped | Escaped — and the UI rework additionally isolates direction per value |

---

## Suggested order of work

1. **#2, #1** — rebuild the venv so the test suite runs again.
2. **#3** — replace the blocklist with a read-only session; this is the sharpest edge.
3. **#4** — add auth, or bind to localhost until it exists.
4. **#6, #7, #8** — the three silent data-loss / wrong-answer bugs.
5. **#9** — collapse the duplicate retrieval paths.
6. **#11, #12, #13** — move long jobs off the request path and stop masking outages.
