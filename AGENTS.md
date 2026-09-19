# AGENTS.md — BIM-Intellect v2

FastAPI + Neo4j + ChromaDB RAG over IFC/BIM models, with a no-build vanilla-JS frontend (English/Persian, RTL). Entrypoint: `main.py` → API in `api/routes.py` + `api/sustainability_routes.py` (mounted under `/api`). Deep docs: `README.md`, `docs/`, and `3D_VISUALIZATION.md`.

## Commands

```bash
# Python (local ./venv, gitignored; Python 3.12)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/bin/python -m pytest tests/ -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/bin/python -m pytest tests/test_model_scene_api.py -q          # single file
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/bin/python -m pytest tests/test_scene_export.py::test_scene_key_is_stable_and_filename_safe -q

python -m compileall -q api bim_graph rag sustainability main.py extract_graph.py extract_sotreys_type.py
node --check static/app.js && node --check static/i18n.js && node --check static/sustainability.js
node --input-type=module --check < static/viewer.js   # viewer.js is ESM — plain `node --check` FAILS on it
python -m uvicorn main:app --reload                    # app on :8000
docker compose -f docker-compose.yml up -d             # Neo4j (auth neo4j/bimintellect)
```

No lint/typecheck config exists — compileall + node checks are the checks. Set `OPENROUTER_API_KEY` in `.env` (copied from `.env.example`); LLM-dependent tests skip or fail on transport errors, not skip, when the network is down.

## Commit trap: tests are whitelisted in .gitignore

`.gitignore` ignores `tests/*` and re-includes files by name. **A new test file silently won't commit** until you add `!tests/test_your_file.py` to that whitelist. Same for fixtures under `tests/fixtures/`.

## 3D visualization — current known bug (start here)

Read `3D_VISUALIZATION_CURRENT_ISSUE_AND_TEST_QUESTIONS.md` (483 lines, full diagnosis + 8 test questions). Summary:

- **Symptom**: clash questions show *"Showing bounding boxes: this project has no exported 3D geometry"* — misleading: `dataset/ifc/scenes/default-project/manifest.json` proves 10 GLB scenes / 499 elements / 0 failed exports exist (`model_0_structure-…`: storey `Storey` → `06d3131f397d`; `model_0_arc-…`: `Ebene 1–9`).
- **Diagnosis**: browser requests `/api/model/elements?...&scene_key=unassigned` instead of a real storey scene. The broken chain is **element → `storey_name` → `scene_key` → manifest lookup**; the exporter/manifest are confirmed fine. Touchpoints: `bim_graph/visualization.py` (`harvest_elements`, `scene_keys_for`), `api/routes.py` (`_scene_scope`), `static/app.js` (`showInViewer` ~line 577 and a second scene_key/boxFallback block ~line 1989).
- **Expected flow**: IFC GUID → `storey_name` (`Ebene 5`) → `scene_key` → `GET /api/model/scene/<project>/<file>/<scene_key>` → load GLB → find node by IFC GlobalId → highlight. `unassigned` is legitimate only for storey-less elements.
- **Verify**: in `POST /api/ask` check `visualization.highlight[].storey_name` (null/empty for elements that live in `Ebene N` = root cause confirmed); network tab must show `GET /api/model/scene/...` not just `elements?scene_key=unassigned`.
- **Acceptance**: GUIDs returned → correct project/file scope → real storey resolved → scene exists in manifest → GLB requested and loads → GUID found → elements visibly highlighted → box fallback only when no scene genuinely exists → UI never claims "no 3D geometry" when scenes exist.
- **Test data**: chat questions are pinned to real clash pairs (e.g. `1$DZROIQX0RgNTaHr7NfS$` arc vs `39hMvtcAHFA8CweEUbIYMM` structure, metric 8.3768) — use the doc's §7 wording.

## 3D data flow (file map)

`bim_graph/visualization.py` (element identity harvest → `scene_keys_for`) → `bim_graph/scene_export.py` (`scene_key` = sha256(storey_name)[:12]; `unassigned` bucket; **glTF serializer flushes only from its C++ destructor — release the object, then verify the glTF length header matches file size**) → `api/routes.py` (`/model/manifest|scene|elements`) → `static/app.js` `showInViewer` → `static/viewer.js` (highlights by glTF node name = IFC GlobalId). glTF axis mapping: `(x, z, -y)`, Y-up, project file units (not metres).

## Environment gotchas

- Neo4j is often not running here (bolt 7687 refused) — graph-backed tests skip gracefully or use fakes; live regression tests need `docker compose -f docker-compose.yml up -d` first.
- Reference IFC models live in gitignored `bim-intellect-sources/` (e.g. `210_King_Merged.ifc`, 148 MB); clash CSVs are local user exports, not committed.
- Generated scenes (`dataset/ifc/scenes/`, ~88 MB/model) are gitignored and rebuilt by project ingestion; `build_scenes: false` on the ingest request skips them.
- If an agent session runs as root, end with `chown -R ubuntu:ubuntu .` — root-owned files break the user's VS Code git (`insufficient permission for adding an object to repository database`).
- `dataset/ifc/project_registry.json` is the source of truth for project/file ids (safe_id-normalized); `safe_id` is idempotent.

## Conventions

- Evidence-first: answers fail closed on unverifiable regulatory claims; never let an LLM choose which elements to highlight — identity comes from deterministic Cypher (`visualization_cypher` on plans) or from result rows.
- All UI strings go through `static/i18n.js` (`en` + `fa` tables must stay in parity; tests assert key counts). Frontend has no bundler: three.js is vendored under `static/vendor/three/` + importmap in `templates/index.html`; cache-bust via `?v=N` query strings when editing static files.
- Frontend contract tests grep source text (e.g. `payload.project_id = activeProjectId` in `tests/test_sustainability_*`) — check `tests/test_sustainability_phase3.py` and `tests/test_sustainability_leed_rag.py` before renaming JS symbols.
