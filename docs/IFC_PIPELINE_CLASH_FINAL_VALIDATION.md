# IFC Pipeline and Clash Final Validation

Validation date: 2026-09-17
Repository: BIM-Intellect v2
Runtime: Windows, Python 3.12, IfcOpenShell, Neo4j 5.20

This report records the final validation of IFC upload/ingestion, federation,
graph replacement, project and file scoping, stale-result removal, AABB clash
detection, issue provenance, unit handling, the Results tab, and generated 3D
scenes. The original IFC files under `dataset/` were read only. The production
development graph, registry, CSVs, and scenes were backed up before destructive
tests and restored afterward.

## Executive result

The reported model-0 federation failure was real. The previous validator treated
regenerated project/site GUIDs as decisive even when the two discipline exports
had compatible units, world contexts, placements, and building identity. The
validator now accepts that pair without globally weakening federation safety.

Additional confirmed defects were found in cross-scope issue cleanup, global
reset bookkeeping, scene-manifest cleanup, Results API/UI scoping, issue
provenance, geometry-worker sizing, and strong-model output limits. They were
fixed and covered by regression tests. The final full suite passed with 270 tests,
one intentional skip, and no failures.

The clash engine remains deliberately AABB-based. This validation does not claim
exact mesh or solid collision.

## Test environment and preservation controls

- Neo4j container: `neo4j:5.20`, service `bim-neo4j`.
- IfcOpenShell geometry settings use world coordinates and explicitly keep
  `CONVERT_BACK_UNITS=False`; extracted geometry is therefore in the pipeline's
  canonical metre coordinate system.
- Real source files were never modified. Uploaded working copies were created by
  the existing project registry only.
- Before destructive graph tests, the live graph, `project_registry.json`, CSVs,
  and generated scenes were backed up outside the repository.
- After the scenarios, the original state was restored and verified as:
  11,007 nodes, 96,474 relationships, 11,005 `Element` nodes, 10,887 AABBs,
  56,204 `CLASH` relationships, and 15,754 `CLEARANCE_VIOLATION`
  relationships. The restored default project still has 45 GLBs totaling
  88,376,868 bytes.

## Dataset inspection

### Metadata and extraction inventory

`Scale` is the declared IFC length-unit scale to metres. `Contexts` summarizes
model representation contexts; `I` means an identity world-coordinate matrix.
Element count is the number of semantic `IfcElement` objects. Extraction counts
include the additional graph/spatial nodes emitted by the extractor.

| IFC | Schema | Scale | Project GUID | Site GUID | Contexts / true north | Map / site georef | IFC elements | Full extraction / usable AABB | Extracted world AABB in metres |
| --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- |
| `model_0_arc.ifc` | IFC2X3 | 1 | `0GqKY_z0n1nQ$0oZfG27fB` | `0GqKY_z0n1nQ$0oZfG27f9` | 5 I / `[0,1]` | no map; Berlin-like lat/lon | 406 | PASS: 418 nodes / 350 | x -2.0187..22.3113; y -4.5013..12.1987; z -0.5..19.98 |
| `model_0_structure.ifc` | IFC2X3 | 1 | `2oRonqqYP1AA1_cD4TRrHj` | `3fOMAA4mfEswpZeKJJCt6a` | 1 I / absent | no map or georef | 149 | PASS: 153 / 149 | x 3.216951..18.312951; y -4.501331..12.298669; z -2.6..20.100001 |
| `model_1_arc.ifc` | IFC2X3 | 0.001 | `0IRQt$Z39AGOGXT2_bGIH9` | `0IRQt$Z39AGOGXT2_bGIHB` | 5 I / `[0,1]` | no map; lat/lon present | 1,040 | PASS: 1,049 / 908 | x -124.6445..139.1673; y -105.1403..89.8326; z -1..21.617 |
| `model_1_structure.ifc` | IFC2X3 | 0.001 | `3nVezS0DT8qv5zLvDuuoFV` | `3nVezS0DT8qv5zLvDuuoFT` | 5 I / `[0,1]` | no map; same lat/lon as arc | 251 | PASS: 262 / 251 | x -7.5384..6.8116; y -10.3243..8.0257; z -1..21.617 |
| `model_3_arc.ifc` | IFC2X3 | 1 | `2scbkp4mn39vI7scRSojiX` | `2scbkp4mn39vI7scRSojiZ` | 5 I / `[0,1]` | no map; lat/lon present | 721 | metadata/federation tested; no full extraction | not measured |
| `model_3_structure.ifc` | IFC2X3 | 1 | same as arc | same as arc | 5 I / `[0,1]` | same as arc | 152 | metadata/federation tested; no full extraction | not measured |
| `model_4_arc.ifc` | IFC2X3 | 0.001 | `2YB5YciPXBZBJJ9DAD_Irq` | `2YB5YciPXBZBJJ9DAD_Irs` | 5 I / `[0,1]` | no map; lat/lon present | 596 | metadata/federation tested; no full extraction | not measured |
| `model_4_structure.ifc` | IFC2X3 | 0.001 | `1tzQwaShP0IuN0aK9_$btD` | `1tzQwaShP0IuN0aK9_$btF` | 5 I / `[0,1]` | no map; same lat/lon as arc | 67 | metadata/federation tested; no full extraction | not measured |
| `model_5_arc.ifc` | IFC2X3 | 1 | `2PDX7De5fEkQK2WGFiemK6` | `2PDX7De5fEkQK2WGFiemK4` | 5 I / `[0,1]` | no map; lat/lon present | 530 | metadata/federation tested; no full extraction | not measured |
| `model_5_structure.ifc` | IFC2X3 | 1 | same as arc | same as arc | 5 I / `[0,1]` | same as arc | 702 | metadata/federation tested; no full extraction | not measured |
| `model_7_arc.ifc` | IFC2X3 | 0.001 | `1FWUByPTDDcgu1RpQ8JA$f` | `1FWUByPTDDcgu1RpQ8JA$h` | 5 I / `[0,1]` | no map; lat/lon present | 518 | metadata/federation tested; no full extraction | not measured |
| `model_7_structure.ifc` | IFC2X3 | 0.001 | `2MWZ6GsIbCeAyE_ggIMlIR` | `2MWZ6GsIbCeAyE_ggIMlIP` | 5 I / `[0,1]` | no map; conflicting lat/lon warning | 482 | metadata/federation tested; no full extraction | not measured |
| `model_8_arc.ifc` | IFC4 | 0.001 | `344O7vICcwH8qAEnwJDjSU` | `20FpTZCqJy2vhVJYtjuIce` | 4 I / `[0,1]` | no map; lat/lon present | 662 | PASS: 669 / 662 | x -0.5415..16.5415; y -0.5415..16.5415; z -0.21..13.0547 |
| `model_8_structure.ifc` | IFC4 | 1 | `0UXhTBwlz5oAgtM67EA0kZ` | `0UXhTBwlz5oAgtM67EA0kX` | 5, WCS z+3 / `[0,1]` | no map; different georef | 182 | PASS: 190 / 182 | x -1.0705..17.1295; y -0.1782..16.15; z -3.45..10 |
| `210_King_Merged.ifc` | IFC2X3 | 0.3048 | 3 project GUIDs | 3 site GUIDs | 15 I / `[2,0,1]` | no map; 3 georefs | 10,957 | existing graph PASS: 11,005 / 10,887 | x -441.92..170.84; y -249.93..206.02; z -9.708..138.162 |
| `AdvancedProject.ifc` | IFC2X3 | 0.001 | `0JBeWf23HFRgZonzIpxJgI` | 18 site GUIDs | 5 I / `[0,1]` | no map; one georef | 1,276 | metadata only; no full extraction | not measured |

Notes:

- Model 0 architecture and structure both identify `Project_0` / `Building_0`,
  use metre units, have compatible identity world contexts and placements, and
  overlap in world geometry. Their regenerated project, site, and building GUIDs
  are different.
- Model 1's declared millimetre units were independently verified. A real wall's
  IfcOpenShell output remains below 100 in absolute coordinate magnitude, proving
  that source millimetres are not leaking into the canonical geometry.
- `210_King_Merged.ifc` declares a 0.3048 source scale, while direct shape checks
  and the existing graph behave as already-normalized building coordinates. This
  merged file was not used as evidence for cross-unit federation.
- No file in this inventory exposes `IfcMapConversion`. Synthetic regression
  fixtures cover matching and conflicting explicit map conversion metadata.

## A. Confirmed problems that were fixed

### FIXED — Federation false negative for discipline exports

- **Root cause:** the old decision was too dependent on project/site GUIDs and a
  narrow metadata fingerprint. Independent exporters legitimately regenerated
  those GUIDs for `model_0_arc` and `model_0_structure`.
- **Change:** `bim_graph/coordinate_system.py` now validates finite/equal declared
  units, internal and cross-file world transforms, true north, explicit map
  conversions, site/building placements, GUID evidence, and non-generic project
  plus building identity. A shared GUID remains strong evidence but is no longer
  the only evidence. Matching origins alone, placeholder georeferences, generic
  names, malformed metadata, ambiguous contexts, and meaningful transform
  differences still fail closed.
- **Validation:** the real model-0 pair passes with basis
  `shared_project_and_building_identity`; model 8 fails on unit mismatch; 42
  cross-building architecture/structure combinations all fail. Unit tests cover
  shared site GUID, shared map conversion, incomplete metadata, transform
  mismatch, unit mismatch, unrelated buildings, generic labels, and malformed
  metadata.
- **Result:** compatible discipline exports federate; clearly unsupported or
  incompatible combinations remain rejected.

### FIXED — Global graph wipe left registry and scene state stale

- **Root cause:** Neo4j was wiped, but registry records belonging to other
  projects could remain marked `ingested`, and their scene manifests/GLBs could
  remain discoverable.
- **Change:** `mark_all_files_not_in_graph()` atomically resets every graph-backed
  registry record before successful selected files are marked ingested.
  `reconcile_scene_scope()` removes manifests/directories outside the new global
  graph scope. Reconciliation occurs before optional analysis so an analysis
  failure cannot leave the viewer advertising old geometry.
- **Validation:** reset orchestration regression tests plus live Scenario A/D.
  A model-0-structure-only reset produced exactly 153 elements, 149 AABBs, one
  `IFCModel`, and only its fresh 665 issues (567 clashes, 98 clearances).
- **Result:** graph, registry, scenes, and Results now agree after a full wipe.

### FIXED — Scoped reruns could retain boundary and zero-result issues

- **Root cause:** cleanup required both relationship endpoints to be selected,
  ran only in some project-scoped paths, and did not reliably cover legacy
  relationships without `r.projectId`. A stale A–C issue could survive when A
  was reanalyzed and C was outside the new selection.
- **Change:** every run executes scoped cleanup before writes, including a run
  that detects zero issues. Cleanup removes a relationship when either endpoint
  touches a selected file, respects project isolation, and recognizes legacy
  relationships through endpoint project IDs. The post-run summary requires both
  endpoints to belong to the requested result scope.
- **Validation:** automated boundary, zero-result, and legacy tests; live
  synthetic scopes A, A+B, and A+B+C; a zero-result D rerun removed an injected
  D–C stale issue while preserving an unrelated project's relationship.
- **Result:** selected-scope results are replacement results, not an accumulation
  of historical rows.

### FIXED — Results endpoints and UI could show an old or broader scope

- **Root cause:** `/filters/storeys`, `/filters/types`, and issue endpoints did
  not consistently accept/enforce selected file IDs. The browser could also
  commit a late response after project/file selection had changed.
- **Change:** Results/filter APIs now apply `projectId` and repeated `file_id`
  parameters. Both issue endpoints must be within the selected files. The UI
  clears/refreshes results when project/file scope changes, builds a stable scope
  key, and discards late responses using request tokens.
- **Validation:** backend query regression, frontend contract regression, JS
  syntax checks, OpenAPI parameter inspection, and a live scoped request that
  returned 665 model-0-structure-only rows and matching filters.
- **Result:** All Issues, Clashes, and Clearances follow the current selection.

### FIXED — Persisted issue provenance was incomplete

- **Root cause:** source file IDs and disciplines existed on endpoints but were
  not persisted on every `CLASHES_WITH` relationship or returned by every issue
  query.
- **Change:** relationships and APIs now retain/return project ID, composite
  endpoint IDs, IFC GUIDs, source file IDs, source filenames, disciplines,
  cross-file flag, issue type, and metric. Existing fields remain compatible.
- **Validation:** same-file and cross-file unit tests and a real model-0 cross-file
  sample. The pair produced 2,408 issues: 1,668 clashes and 740 clearances; 1,429
  were cross-file. All sampled cross-file rows carried both file IDs/names,
  disciplines, endpoint GUIDs, project, issue, metric, and `crossFile=true`.
- **Result:** every returned issue can be traced to both IFC sources.

### FIXED — Clearance unit meaning was ambiguous

- **Root cause:** comments/UI described `0.25` as file/model units or feet even
  though the extraction setting produces canonical SI geometry.
- **Change:** the engine declares a physical
  `CLEARANCE_THRESHOLD_METRES = 0.25`, pins the IfcOpenShell unit-conversion
  contract, derives the coordinate threshold explicitly, records source unit
  scale separately in scene metadata, and labels metrics in metres / cubic
  metres. Federation still rejects unequal declared source units.
- **Validation:** metre and millimetre real IFC checks plus unit tests for the
  geometry setting and threshold. Model-1 millimetre geometry extracts at normal
  metre-scale coordinates.
- **Result:** clearance is consistently 0.25 m in extracted coordinates; stored
  geometry was not silently reinterpreted.

### FIXED — Large geometry extraction overcommitted native workers

- **Root cause:** using every logical CPU caused the 187 MB `model_1_arc.ifc`
  native iterator to terminate partway through extraction in this environment.
- **Change:** extraction defaults to at most four geometry workers and supports
  bounded `BIM_GEOMETRY_WORKERS` configuration.
- **Validation:** worker-bound regression tests and a complete real extraction of
  `model_1_arc`: 1,049 nodes, 1,048 edges, 908 AABBs in 345.56 seconds.
- **Result:** the previously terminating large-file extraction completes.

### FIXED — Strong-model mode fell back despite available models

- **Root cause:** router and final model slugs were available through OpenRouter,
  but the final Claude request omitted `max_tokens`. OpenRouter performed its
  affordability check against the provider's full 64,000-token output allowance
  and returned HTTP 402. The application then correctly emitted the Persian safe
  fallback: `مدل تولید پاسخ در دسترس نبود...`.
- **Change:** router, contextual-understanding, conversation, grounded-final,
  citation-repair, and completeness-repair calls always send explicit output
  limits. Defaults are 1,200 router tokens and 4,096 final tokens, configurable
  with `RAG_ROUTER_MAX_TOKENS` and `RAG_FINAL_MAX_TOKENS`.
- **Validation:** direct provider model checks through the supplied SOCKS proxy,
  captured-request regression tests, 13 routing tests, and a full strong-mode
  Persian RAG request. The post-fix request completed with Gemini routing, Claude
  final generation, 1,623 answer characters, and three validated citations.
- **Result:** strong mode generates the grounded answer instead of entering the
  provider-unavailable fallback for this affordability error. No credential or
  account identifier is recorded in this report.

## B. Reported issues that were already working correctly

### VERIFIED — NO FIX REQUIRED: Neo4j reset itself is destructive and ordered

The loader executes the reset before new graph writes. Actual graph queries after
the reset showed no previous Elements, IFCModels, or `CLASHES_WITH`
relationships. The required fix was synchronization of registry/scenes, not the
Neo4j deletion primitive.

### VERIFIED — NO FIX REQUIRED: normal no-wipe project isolation

With `reset_all=false`, adding model-1 structure left model-0 untouched. Narrowing
model-0 from architecture+structure to structure retained the unrelated model-1
project at exactly 262 nodes and 1,024 issues while model-0 architecture nodes and
out-of-scope issues became zero. Registry and scene manifest followed the
narrower model-0 scope.

### VERIFIED — NO FIX REQUIRED: candidate fetching already honors selection

The UI sends selected file IDs, the registry resolves only those records, and
`_fetch_elements` filters by project and `sourceFileId`. Synthetic tests observed
2 elements/1 issue for A, 3/3 for A+B, and 4/6 for A+B+C; no unselected element
entered detection. The necessary change was stale-boundary cleanup and Results
filtering, not candidate generation.

### VERIFIED — NO FIX REQUIRED: composite IFC identity

Graph element IDs are composite across project/file boundaries while the original
IFC GlobalId remains provenance. Duplicate exports of the same GlobalId are not
compared as independent clash candidates. Same-file and cross-file cases remain
distinguishable.

### VERIFIED — NO FIX REQUIRED: current AABB classification rules

Tests confirm, per axis,
`gap = max(minA, minB) - min(maxA, maxB)`:

- three negative gaps produce `CLASH` and the metric is overlap volume;
- touching boxes produce `CLEARANCE_VIOLATION` with metric zero;
- separated boxes below 0.25 m produce a clearance violation;
- exactly 0.25 m produces no issue;
- metrics are rounded to four decimals;
- ignored IFC type pairs, duplicate GUID exclusion, cross-file flags, and
  sweep-and-prune candidate bounds behave as documented.

### VERIFIED — NO FIX REQUIRED: real geometry is primary in the viewer

For the real model-0 pair, ingestion generated 10 valid GLBs totaling 2,306,828
bytes. Their glTF JSON contained 499 IFC-GlobalId node names, exactly matching the
499 graph elements with usable geometry. Manifest, scene, and selected-file
element endpoints returned the expected project/file scope. The restored
`210_King_Merged` project serves 45 valid GLBs. Viewer inspection confirms GLB
geometry is attempted first and AABBs are created only when an exact exported
scene is unavailable.

### VERIFIED — NO FIX REQUIRED: unrelated product areas

The full suite exercises RAG v3 routing/retrieval/citations, Graph RAG,
conversation memory, Persian/English and RTL/LTR UI contracts, Sustainability,
LEED document retrieval, single/batch IFC upload, registry behavior, and API
compatibility. All executed tests passed. No unrelated subsystem was redesigned.

## C. Remaining limitations by design

- AABB overlap is not exact solid or mesh collision detection.
- AABB can report false positives, especially for rotated, concave, or sparse
  geometry.
- Exact clash points and intersection solids are not implemented.
- Penetration depth based on exact solids is not implemented; a clash metric is
  AABB overlap volume.
- Discipline/system-specific clearance rule profiles are not implemented; the
  current physical clearance is the single 0.25 m rule, subject to the existing
  ignored-type pairs.
- AABB fallback in the 3D viewer is not exact IFC geometry. It is used only when
  generated GLB geometry is unavailable.
- A federation can only be accepted from deterministic evidence exposed by the
  IFCs. Files with incomplete metadata and no trustworthy shared identity still
  fail closed even if their numeric extents happen to overlap.
- Full browser visual automation was not available; frontend behavior was checked
  through deterministic JS/API tests, syntax validation, manifest/GLB inspection,
  and source-flow review.

## D. Dataset validation matrix

| Dataset / file combination | Upload | Federation | Extraction | Graph | Clash | 3D | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `model_0_structure` only | PASS | single-model | PASS, 153/149 | reset scope exactly one model | 665 scoped issues | structure scenes/manifest scoped | PASS |
| `model_0_arc + model_0_structure` | PASS | PASS despite different GUIDs | PASS, both | 571 graph nodes in combined extraction scope | 2,408; 1,429 cross-file | 10 GLBs, 499 named geometry nodes | PASS |
| `model_0_structure + model_1_arc` | source files valid | REJECT: units/building differ | each independently valid | not mixed | not run by design | not federated | EXPECTED REJECTION |
| `model_1_arc + model_1_structure` | source files valid | PASS: project/building identity | PASS, both independently | structure used in isolation scenario | selection isolation verified | not generated in this validation | PASS with noted large-file worker cap |
| `model_3_arc + model_3_structure` | source files valid | PASS: shared project GUID | metadata only | not live-loaded | not run | not generated in this validation | FEDERATION PASS ONLY |
| `model_4_arc + model_4_structure` | source files valid | PASS: project/building identity | metadata only | not live-loaded | not run | not generated in this validation | FEDERATION PASS ONLY |
| `model_5_arc + model_5_structure` | source files valid | PASS: shared project GUID | metadata only | not live-loaded | not run | not generated in this validation | FEDERATION PASS ONLY |
| `model_7_arc + model_7_structure` | source files valid | PASS with georef warning; local frame/identity agree | metadata only | not live-loaded | not run | not generated in this validation | FEDERATION PASS WITH WARNING |
| `model_8_arc + model_8_structure` | source files valid | REJECT: 0.001 vs 1 units (also WCS/georef differences) | PASS independently | not mixed | not run by design | not federated | EXPECTED REJECTION |
| 42 cross-building arc/structure pairs | source files valid | all REJECTED | not required for rejection | not mixed | not run | not federated | EXPECTED REJECTION |
| `210_King_Merged` | existing registered copy | single merged model | existing PASS | restored 11,005 Elements | restored 71,958 issues | 45 GLBs / 88,376,868 bytes | PASS |
| `AdvancedProject` | source and registered copy present | single-model metadata valid | not fully extracted here | not changed | not run | not generated here | METADATA ONLY |

## E. Test summary

### Automated tests

- Focused validation command: **97 passed, 0 failed, 0 skipped** in 46.74 s.
- Standalone routing regression: **13 passed, 0 failed** in 3.95 s.
- Full Python suite: **270 passed, 0 failed, 1 skipped**, 8 deprecation
  warnings, in 100.31 s.
- The one skip is an existing environment/fixture-conditioned test, not a
  suppressed failure.
- Python compilation: PASS for `api`, `bim_graph`, `rag`, `sustainability`, and
  entry scripts.
- JavaScript syntax: PASS for `static/app.js`, `static/viewer.js`, and
  `static/i18n.js`.
- OpenAPI smoke: PASS, 34 paths; `/api/issues` exposes `project_id`, repeated
  `file_id`, `storey`, and `types`.
- Docker Compose: PASS for both `docker-compose.yml` and
  `docker-compose.client.yml`. The default file emits only Docker's existing
  obsolete top-level `version` warning.
- `git diff --check`: PASS; only Git's Windows line-ending notices were emitted.

The focused command was:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_multifile_workflows.py `
  tests/test_ifc_pipeline_final_validation.py `
  tests/test_rag_v2.py tests/test_sustainability_phase1.py
```

Third-party pytest plugin auto-loading was disabled because a globally installed
Hydra/ANTLR plugin is binary-version incompatible and fails during pytest startup
before this repository is collected. No project plugin or test was disabled.

Full suite:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q
```

### Integration and real-data checks

- Six real IFCs completed full extraction: model-0 arc/structure, model-1
  arc/structure, and model-8 arc/structure.
- Fourteen discipline files plus `210_King_Merged` and `AdvancedProject` were
  parsed for schema, units, identifiers, contexts, true north, maps/georeference,
  and semantic element counts.
- Five end-to-end Neo4j scenarios (A–E) were executed, including destructive
  reset, combined discipline ingestion, incompatible rejection, global
  replacement, and no-wipe narrowing/preservation.
- Three synthetic file scopes (one, two, all) and a zero-result rerun were checked
  directly in Neo4j.
- Live REST/OpenAPI checks covered manifests, GLB serving, selected-file elements,
  Results scope, provenance, filters, and ingest request shape.
- 3D checks validated 10 newly generated model-0 GLBs and 45 restored reference
  GLBs, binary declared lengths, JSON chunks, manifest scope, and IFC GlobalId
  node names.
- Strong-mode provider validation used the supplied SOCKS proxy. Model discovery,
  router completion, final completion, and one full Persian grounded query passed
  after the token-cap fix.

Useful repeatable checks:

```powershell
python -m compileall -q api bim_graph rag sustainability extract_graph.py extract_sotreys_type.py main.py
node --check static/app.js
node --check static/viewer.js
node --check static/i18n.js
docker compose config --quiet
docker compose -f docker-compose.client.yml config --quiet
```

For provider checks in a new shell, use the requested proxy without printing the
API key:

```powershell
$env:HTTP_PROXY='socks5://127.0.0.1:10808'
$env:HTTPS_PROXY='socks5://127.0.0.1:10808'
$env:ALL_PROXY='socks5://127.0.0.1:10808'
```

## F. Files changed

- `.env.example` — documents explicit router/final output limits and the bounded
  IFC geometry-worker setting.
- `.gitignore` — permits the final IFC validation regression module.
- `README.md` — documents SI geometry, issue provenance, token limits, and current
  AABB limitations.
- `REPORT.md` — aligns operational unit and visualization statements.
- `BIM-Intellect-Documentation.md` — aligns the main technical documentation.
- `api/routes.py` — global-reset synchronization, selected-file Results/filter
  scope, and complete provenance fields.
- `bim_graph/clash_pipeline.py` — SI threshold contract, endpoint-touching stale
  cleanup, zero/legacy cleanup, scoped summaries, and relationship provenance.
- `bim_graph/coordinate_system.py` — conservative multi-signal federation
  inspection and validation.
- `bim_graph/project_registry.py` — atomic global graph-state reset for registry
  records.
- `bim_graph/scene_export.py` — safe global scene reconciliation and explicit
  geometry-unit metadata.
- `docs/3D_VISUALIZATION.md` — exact-scene/fallback and SI behavior.
- `docs/MULTI_FILE_WORKFLOWS.md` — federation, reset, scope, and unit behavior.
- `docs/IFC_PIPELINE_CLASH_FINAL_VALIDATION.md` — this reproducible validation
  record.
- `extract_graph.py` — pinned unit behavior and bounded configurable geometry
  workers.
- `rag/config.py` — configurable router/final output limits.
- `rag/orchestrator.py` — sends output caps on every OpenRouter completion path.
- `static/app.js` — Results scope parameters, state invalidation, and stale-response
  protection.
- `static/i18n.js` — unit-correct bilingual Results labels.
- `static/viewer.js` — clarifies canonical metre coordinates and AABB fallback.
- `templates/index.html` — unit-correct Results metric heading.
- `tests/test_ifc_pipeline_final_validation.py` — reset, scoping, cleanup,
  provenance, unit, worker, real IFC, Results, and frontend regressions.
- `tests/test_multifile_workflows.py` — expanded federation safety and multi-file
  provenance coverage.
- `tests/test_rag_v2.py` — explicit completion-limit regression.
- `tests/test_sustainability_phase1.py` — updated canonical-metre contract.

## G. Final client-readiness conclusion

Within the current AABB methodology, this build is ready for final client
validation for the tested workflows:

- **Verified:** Wipe replaces prior graph analysis with only newly selected IFCs
  and synchronizes registry and 3D assets.
- **Verified:** only selected IFC files participate in candidate generation,
  stored issues, summaries, Results APIs, and viewer element scope.
- **Verified:** stale clash/clearance Results are removed on scope changes and on
  zero-result reruns.
- **Verified:** compatible architecture/structure exports such as model 0 can
  federate despite regenerated IFC project/site GUIDs.
- **Verified:** incompatible source units and unrelated buildings remain rejected.
- **Verified:** issue provenance identifies both source IFC files and endpoint
  GUIDs for same-file and cross-file cases.
- **Verified:** the 0.25 clearance threshold is unit-safe against the canonical
  metre extraction contract.
- **Verified:** generated GLB geometry is served and used when available; AABBs
  remain an explicit fallback.
- **Verified:** AABB remains the documented clash methodology and is not presented
  as exact physical collision.

The conclusion is limited to the datasets and checks recorded above. Exact
mesh/solid collision, discipline-specific rule libraries, and browser-level
pixel/interaction automation remain outside this task.
