# Client Final UI and File-Management Improvements

Validation date: 2026-09-18
Workspace: `bim-intellect-v2`
Python: 3.12

## Scope and audit findings

The implementation was inspected before changes were made. Several requested building blocks already existed:

- Clash result rows already returned the legacy `a_*` and `b_*` endpoint fields, source filenames, disciplines, project ID, and `cross_file`.
- Element graph IDs are composite (`project::source-file::IFC-GUID`) and the separate `ifcGuid` property is retained.
- Result queries were already project/file scoped, and clash generation already wrote endpoint file provenance.
- A general `.table-container` already enabled `overflow-x: auto`, but the Results table had no content-preserving minimum width or accessible scroll-region treatment.
- RAG replacement uploads could delete one document internally by `document_id`, but there was no client/API operation for selected single or bulk deletion.
- The IFC registry and scene exporter already provided the primitives needed for project-scoped registry updates and generated-scene pruning.
- The Pipeline checkboxes already controlled analysis scope. Deletion selection is deliberately separate so deselecting a model for analysis cannot be confused with deleting it.
- Full RAG clear, project-scoped clash analysis, graph wipe, scene manifests, Sustainability, bilingual UI, and RTL switching already existed and were preserved.

## Implementation decisions

### Complete Results provenance

The three Results APIs retain every legacy field and now add client-friendly aliases:

- `element_a_id`, `element_a_guid`, `element_a_name`, `element_a_type`
- `element_a_source_file`, `element_a_source_file_id`, `element_a_discipline`
- equivalent `element_b_*` fields
- `relation_scope`, always `CROSS-FILE` or `INTRA-FILE`

The query falls back to relationship provenance for legacy nodes whose source properties are absent. `Element.id` and `ifcGuid` remain separate. The frontend renders 13 explicit columns so both endpoint identities, filenames, and disciplines are visible.

### Horizontal Results scrolling

The table is inside a keyboard-focusable region with localized `aria-label`. It retains `overflow-x: auto`, adds touch momentum/inline overscroll containment, and gives the table a `1760px` minimum width. Long composite IDs wrap inside bounded LTR-isolated cells. Direction-specific rules keep English scrolling LTR and Persian scrolling RTL without changing vertical page behavior.

### PDF deletion

`POST /api/rag/documents/delete` accepts stable `document_ids`. The embedder resolves and deletes only chunk IDs whose Chroma metadata has the selected `document_id`; it never rebuilds or clears the collection. The response includes deleted document/chunk counts and the refreshed document list/counts.

The Documents UI now provides per-document checkboxes, Select All, disabled-until-selected Delete Selected, confirmation, refreshed counts/list, and selection reset. Clear All remains available.

### IFC deletion and cleanup semantics

`POST /api/ifc/projects/{project_id}/files/delete` accepts registered `file_ids`. Before mutation it verifies that every requested ID belongs to that project and every stored path is inside the managed project directory. Under the existing pipeline lock it performs:

1. project/file-scoped Neo4j cleanup;
2. registry removal;
3. generated-scene manifest/directory pruning;
4. deletion of only the managed uploaded IFC paths.

Neo4j cleanup deletes:

- selected-file `Element` nodes and their `HAS_ELEMENT`, containment, material, and issue relationships through `DETACH DELETE`;
- all same-file or cross-file `CLASHES_WITH` relationships touching a deleted element;
- selected `IFCModel` nodes and `HAS_MODEL` relationships;
- the `BIMProject` node only when no models remain;
- file-owned quantity/material evidence;
- Sustainability runs/results whose selected scope included a deleted file.

It intentionally does **not** perform a global orphan-material sweep. Sibling models, unrelated projects, and unrelated documents remain untouched.

Pipeline analysis selection and deletion selection use different state and different checkboxes. The delete flow confirms the destructive operation, clears removed IDs, invalidates/refreshes Results and filters, clears Sustainability output, and cancels/clears pending 3D loads before refreshing project state.

### Navigation and terminology

Visible menu order is now exactly:

1. PDF Documents
2. IFC Pipeline
3. Chat
4. Clash Results
5. Sustainability Score

Internal `data-tab` identifiers, active-state behavior, keyboard DOM order, and mobile navigation logic were not renamed. English and Persian labels were updated consistently. Existing Results IFC Type `ALL` behavior remains: checking selects every type and unchecking clears every type.

## API changes

### `POST /api/rag/documents/delete`

Request:

```json
{"document_ids": ["document-a", "document-b"]}
```

Response adds deletion and refreshed-state details:

```json
{
  "status": "ok",
  "document_ids": ["document-a", "document-b"],
  "deleted_documents": 2,
  "deleted_chunks": 42,
  "deleted_by_document": {"document-a": 20, "document-b": 22},
  "remaining_chunks": 100,
  "remaining_documents": 3,
  "documents": []
}
```

### `POST /api/ifc/projects/{project_id}/files/delete`

Request:

```json
{"file_ids": ["architecture-id", "structure-id"]}
```

The response reports deleted/remaining registry records, physical managed files, graph cleanup counts, and the reconciled scene manifest.

### Results endpoints

`GET /api/clashes`, `GET /api/violations`, and `GET /api/issues` retain old fields and add the `element_a_*`, `element_b_*`, and `relation_scope` fields described above. Existing `project_id`, repeated `file_id`, storey, and type filters remain compatible.

## Regression tests

New tests are in `tests/test_client_file_management.py`; Results alias assertions were also added to `tests/test_ifc_pipeline_final_validation.py`.

Coverage includes:

- single and multiple PDF deletion;
- real temporary Chroma deletion, retained unrelated chunks, refreshed listing, and post-delete retrieval exclusion;
- IFC single/bulk deletion;
- preservation of an unselected sibling registry record, uploaded file, and GLB directory;
- refusal to delete registry paths outside managed project storage;
- graph cleanup of issues touching either selected endpoint;
- project/file scoping for Elements, IFCModel, Sustainability evidence/runs;
- Results provenance aliases and explicit intra/cross-file relation scope;
- distinct analysis/deletion selection state;
- Documents and Pipeline select-all/delete controls;
- Results minimum width, horizontal scrolling, and LTR/RTL rules;
- menu order and English/Persian terminology;
- pending 3D-load invalidation during destructive scope changes;
- OpenAPI exposure of both bulk deletion operations.

## Verification results

### Automated tests

- Focused suite: **29 passed, 0 failed, 0 skipped** in 5.73 seconds (final worktree rerun).
- Full suite: **280 passed, 0 failed, 1 skipped** in 95.05 seconds (final worktree rerun).
- The one skip is `test_cypher_generator_prefers_tag_over_id_for_novel_phrasing`: live OpenRouter/provider access returned HTTP 403 because configured LLM credentials/model permissions were unavailable. This is an external live-credential check, not a product regression.
- A targeted integration rerun identifying the skip produced **77 passed, 1 skipped**.

Pytest was run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` because a globally installed, unrelated Hydra plugin has an incompatible ANTLR runtime and fails before project test collection. Project tests themselves pass.

### Static and service checks

- Python compilation: PASS (`api`, `bim_graph`, `rag`, `sustainability`, `main.py`, `extract_graph.py`).
- JavaScript syntax: PASS for all **5** files under `static/`.
- Frontend smoke: `/` returned 200; `/static/app.js` returned 200.
- OpenAPI generation: PASS, **36 paths**, including both new deletion operations.
- Docker Compose: both `docker-compose.yml` and `docker-compose.client.yml` validated. Docker emitted the pre-existing warning that the top-level `version` key in `docker-compose.yml` is obsolete.
- `git diff --check`: PASS (only Git's Windows LF-to-CRLF notices were emitted).

### Real data and live read-only checks

- The focused/full suites passed the real `model_0_arc.ifc + model_0_structure.ifc` federation validation and real millimetre IFC geometry normalization test.
- Live Neo4j connectivity: PASS; observed 574 nodes, 2 `IFCModel` nodes, and 2,408 issue relationships. This was read-only.
- Live scoped `/api/issues`: PASS for the two ingested model-0 files; 1,000 API rows returned (route limit), all with the additive provenance contract; 670 were cross-file.
- Live 3D manifest: PASS; 2 model entries and 10 scenes.
- Live GLB serving: PASS; tested GLB was 761,424 bytes with valid `glTF` magic.
- Live `/api/model/elements` file scope: PASS; 149 scoped elements returned for the tested scene/file.
- Real Chroma selective deletion integration: PASS in isolated temporary storage. Deleted document IDs were absent from subsequent retrieval; unrelated chunks remained.
- IFC deletion integration: PASS using isolated managed storage, isolated manifests, and a deterministic graph client. Original IFC files under `dataset/` were never modified or deleted.

## Preserved behavior

The full suite and focused regressions cover or smoke-test IFC upload/batch upload, compatible federation, graph wipe, selected-file clash scope, stale clash cleanup, AABB behavior, scene generation/manifest/serving, RAG v3 ingestion/retrieval, Graph RAG query contracts, Sustainability persistence/scope, conversation memory, English/Persian translations, RTL/LTR rules, and existing OpenAPI routes.

## Remaining limitations

- Cross-store deletion spans Neo4j, registry JSON, generated scenes, and the filesystem; these stores cannot participate in one distributed transaction. Graph cleanup is attempted before registry/file mutation, and all mutations are serialized under the pipeline lock, but process failure between stores may require an operator retry.
- A live destructive deletion was deliberately not run against the client's current Neo4j/project registry or real dataset files. Destructive behavior was validated in isolated storage and with deterministic graph assertions.
- RTL/LTR and responsive scrolling were validated through DOM/CSS regression assertions and frontend HTTP smoke checks. No browser automation runtime is installed, so pixel-level mobile/desktop rendering was not claimed.
- The existing AABB clash engine remains AABB-based; exact solid/mesh intersections, exact clash points, and penetration solids are outside this change.
- Sustainability cleanup removes a whole run when its selected file scope included a deleted model; partial recomputation is not attempted automatically.
- Live OpenRouter model generation could not be validated because the configured provider returned HTTP 403.

## Reproduction commands

PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_client_file_management.py tests/test_ifc_pipeline_final_validation.py -q
python -m pytest -q
python -m compileall -q api bim_graph rag sustainability main.py extract_graph.py
Get-ChildItem static -Filter *.js -File | ForEach-Object { node --check $_.FullName }
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.client.yml config --quiet
```

## Files changed for this client improvement

- `api/routes.py` — bulk PDF/IFC deletion APIs and additive Results provenance.
- `bim_graph/project_cleanup.py` — project/file-scoped graph and Sustainability cleanup.
- `bim_graph/project_registry.py` — atomic removal of selected registry records.
- `rag/embedder.py` — selective bulk Chroma deletion by document ID.
- `templates/index.html` — navigation order, management controls, provenance columns, accessible scroll region.
- `static/app.js` — selection/deletion flows, Results rendering, state refresh and invalidation.
- `static/i18n.js` — English/Persian client terminology, confirmations, and accessible labels.
- `static/style.css` — horizontal Results layout and file-management controls.
- `static/viewer.js` — cancel pending scene loads when deleted scope is cleared.
- `tests/test_client_file_management.py` — new API/storage/graph/UI regression coverage.
- `tests/test_ifc_pipeline_final_validation.py` — expanded Results provenance/cache-version assertions.
- `docs/CLIENT_FINAL_UI_FILE_MANAGEMENT_IMPROVEMENTS.md` — this implementation and validation report.
