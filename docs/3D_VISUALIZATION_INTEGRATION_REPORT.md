# 3D Visualization Integration Report

## Integration identity

| Item | Value |
|---|---|
| Source branch | `origin/3D-visualization` |
| Source commit | `88edaa6171612a7b2ad39c8954ef7c63886b272d` |
| Target/current branch | `feature/sustainability` |
| Authoritative pre-merge checkpoint | `b4cd7e6` (`checkpoint: preserve RAG v3 and chat UI improvements`) |
| Safety branch | `backup/pre-3d-integration-20260915` |
| Safety stash | `safety snapshot before 3D integration 2026-09-15` |
| Integration date | 2026-09-15 |

The current branch was treated as authoritative. Its RAG v3 implementation,
evaluation artifacts, retrieval tests, chat Markdown renderer, Persian typography,
and dynamic RTL/LTR behavior were committed before the source branch was merged.
The source branch was then merged into that checkpoint, making the current system
the first parent rather than rebuilding the result from the older 3D branch.

## Pre-merge inspection

The merge base was `0e6df73`, which was also the original target `HEAD` before the
RAG v3/UI checkpoint. The 3D branch contained the target baseline plus its
visualization work.

Current-tree-only work included:

- RAG v3 chunking, structured tables, metadata, retrieval, reranking, Persian
  numeric normalization, completeness repair, and follow-up behavior;
- RAG benchmark fixtures, raw evaluation artifacts, reports, and tests;
- Chat Markdown rendering, content-based direction, Persian typography, and the
  larger auto-resizing textarea;
- the RAG v3 package dependencies and index manifest.

3D-branch-only work included:

- storey-partitioned GLB export and scene-manifest lifecycle;
- deterministic graph-result element harvesting and visualization queries;
- model manifest, scene-file, and element-bounds endpoints;
- `static/viewer.js` plus vendored three.js r180 modules;
- viewer translations, documentation, and focused tests;
- ingestion integration that reuses the existing bounding-box tessellation pass.

The files changed on both sides were `.env.example`, `.gitignore`,
`rag/orchestrator.py`, `static/app.js`, `static/style.css`, and
`templates/index.html`. No new Python dependency was required by the 3D branch;
three.js is vendored and IfcOpenShell was already an application dependency.

## Imported components

| Area | Main files | Integrated behavior |
|---|---|---|
| Scene generation | `bim_graph/scene_export.py`, `extract_graph.py` | Exports one GLB per storey during the existing geometry pass, verifies GLB length, writes manifests atomically, and prunes stale generated scenes. |
| Evidence mapping | `bim_graph/visualization.py`, `bim_graph/graph_retriever.py` | Harvests IFC identities deterministically and keeps visualization separate from citation sources. |
| Query planning | `bim_graph/query_planner.py`, `bim_graph/cypher_templates.py` | Adds bounded identity queries for aggregate answers without changing answer-result columns. |
| HTTP API | `api/routes.py` | Adds manifest, safe scene-file, and graph-bounds endpoints plus the `build_scenes` ingest option. |
| Browser viewer | `static/viewer.js`, `static/vendor/three/` | Provides one reusable WebGL viewer, orbit controls, GLB LRU caching, highlighting, camera framing, and an AABB fallback. |
| Application UI | `templates/index.html`, `static/app.js`, `static/style.css`, `static/i18n.js` | Adds a bilingual Chat-side 3D drawer, per-answer actions, reset/close controls, responsive sizing, and RTL mirroring. |
| Documentation/tests | `docs/3D_VISUALIZATION.md`, four visualization test modules | Documents and verifies export, scoping, endpoint safety, answer payloads, and viewer module resolution. |

## Conflict resolution

### `static/app.js`

The only textual conflict was the Chat message function signature. The resolution
kept the current `resizeChatInput`, content-direction detector, safe Markdown/plain
rendering, citation presentation, and metadata source tags, while adding the
optional visualization payload and per-message viewer button. The 3D code reads
the same Pipeline project/model selection already used by Chat.

### `static/style.css`

The root width variables conflicted. The resolution retained the current wider
reading measure and Persian/English typography variables, then added the viewer
width. All viewer rules use logical properties and therefore mirror in Persian.
The existing mobile rules, Markdown tables/code, and minimum 16 px textarea remain
unchanged.

### `templates/index.html`

The current Noto Sans Arabic/Inter font loading, textarea, Markdown renderer, and
script ordering were preserved. The three.js import map, viewer drawer, canvas,
and module were added. Asset versions were advanced to avoid stale browser caches.

### Automatically merged shared files

- `.env.example`: retained all RAG v3 values and added `BIM_SCENE_STORAGE_DIR`.
- `.gitignore`/`.dockerignore`: retained new RAG test fixtures and ignored generated
  viewer geometry; stale formatting from the source branch was normalized.
- `rag/orchestrator.py`: retained metadata-only answers, completeness validation,
  Persian numeric aliases, follow-up rewriting, and current diagnostics while
  adding graph elements and visualization payloads. Metadata-only responses were
  explicitly updated to retain the visualization contract, and successful
  completeness repairs restore eligible graph highlights.

## Compatibility adaptations

The original 3D branch scoped scenes by project. The combined version also stores
the selected `file_ids` in every answer's visualization payload and applies those
IDs to both `/api/model/elements` and client-side manifest selection. This ensures
that reopening an older response uses its original model selection instead of the
current Pipeline selection.

The visualization block remains additive and separate from `sources`, so existing
citation filtering and API clients are unaffected. Regulation-only answers can
offer a clearly labelled related-type view, but only graph-identified elements
auto-open as evidence. Withheld or invalid answers clear highlights.

No changes were made to the production model profiles, OpenRouter prompts/contracts,
Chroma index format, sustainability calculation contracts, clash formulas, upload
formats, or Neo4j ingestion provenance fields.

## Verification

| Check | Result |
|---|---|
| Full project test suite | **240 passed**, 0 failed, 0 skipped, 8 dependency deprecation warnings; 112.67 s |
| Focused 3D + RAG v3 + Chat UI suite | **157 passed**, 1 environment-path skip before the repository IFC fallback was added |
| Final focused visualization/API scope suite | **74 passed** |
| Metadata-answer visualization contract + real export | **10 passed** |
| Real IFC-to-GLB export | Passed: 155 `IfcWall` elements, one 154,504-byte storey scene, zero export failures |
| Headless browser/WebGL smoke | Passed: Chromium loaded the import map, all vendored modules, manifest, and real GLB; visible geometry rendered |
| FastAPI startup | Passed under Uvicorn on `127.0.0.1:8765` |
| Frontend route/assets | `GET /`, viewer module, three.js core, and GLTF loader returned 200 |
| OpenAPI generation | Passed; 34 paths including all three model endpoints and existing RAG/sustainability endpoints |
| JavaScript syntax | Passed for `app.js`, `i18n.js`, `chat-markdown.js`, and `viewer.js` |
| Python compilation | Passed for `api`, `bim_graph`, `rag`, `sustainability`, `main.py`, and `extract_graph.py` |
| Compose validation | Both Compose files valid; Docker reports the existing obsolete `version`-field warning |
| Merge markers / patch whitespace | None; `git diff --check` passed |

The full suite includes IFC upload/multi-file workflows, graph query planning and
retrieval, clash regression tests, RAG v3 parsing/retrieval/citation behavior,
conversation memory, sustainability APIs/calculation/reporting, visualization
scope, scene APIs, and frontend integration checks.

## Remaining limitations

- Exact GLB geometry is available only after ingestion with `build_scenes: true`;
  older projects fall back honestly to graph AABBs until re-ingested.
- The viewer intentionally caps an answer at four scenes and 500 highlighted
  elements. The UI reports truncation.
- Interactive pointer/orbit behavior was not manually usability-tested, although
  real geometry was rendered in headless Chromium/WebGL.
- The existing Graph RAG layer does not inject project/file predicates into every
  possible LLM-generated Cypher query. Viewer fetches are project/file scoped and
  report unmatched evidence, but globally enforcing graph-query scope would be a
  separate Graph RAG architecture change and was intentionally not introduced by
  this integration.
- Scene endpoints follow the application's existing trusted/private deployment
  model and do not add authentication independently.
- The AABB fallback has the same conservative precision limitations as clash
  detection; it is not an exact intersection solid.

No integration failures remain unresolved.
