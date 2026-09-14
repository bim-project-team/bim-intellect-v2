# 3D Visualization

## Purpose

Every answer that identifies BIM elements is accompanied by a 3D map of the
relevant part of the building, with those elements highlighted. The map is a
presentation of the graph evidence behind the answer, never an independent claim.

Three properties follow from that:

- geometry is generated at ingestion from the same tessellation the clash engine
  uses, so the map cannot disagree with the measurements the platform reports;
- which elements are highlighted is decided by deterministic Cypher, never by a
  language model;
- when there is no element evidence the map says so instead of guessing.

## Pipeline

```text
IFC file
   |
   v
extract_graph.extract()                  filtered element candidates
   |
   v
extract_geometry_for()                   ONE tessellation pass
   |                    \
   |                     \--> SceneWriter --> dataset/ifc/scenes/<project>/<file>/<scene_key>.glb
   v                                          + manifest.json
minX..maxZ bounding boxes
   |
   v
Neo4j Element nodes
```

Scene export is fused into the existing bounding-box pass because tessellation
dominates ingestion cost. Measured on the 10,887-element reference model:

| Work | Time |
|---|---|
| Bounding boxes only | 73 s |
| Bounding boxes + glTF export | 55 s |

The glTF write is free in practice; a separate export pass would have doubled the
most expensive step.

## Partitioning by storey

One asset for the whole model is unusable, and one asset per element is
unmanageable. Storey is both the natural payload boundary and the natural
question boundary ("clearances near doors on Level 5").

Reference model, 45 scenes totalling 88 MB:

| | Elements | glTF |
|---|---|---|
| Mean scene | 242 | 1.96 MB |
| Largest scene | 1,392 | 13.2 MB |
| Level 5 | 236 | 1.56 MB |

`gzip` reduces these by roughly 70%.

### Scene keys

`scene_key(storey_name)` is `sha256(name)[:12]`, not a slug. The reference model
already contains `"BLDG. 1,2,3- LEVEL 5 FLR. FIN."` and
`"BLDG 1,2,3- LEVEL 5 FLR. FIN."` as distinct storeys, which any
punctuation-stripping slug would merge, and Persian storey names would slugify to
nothing. The human-readable name lives in the manifest.

### Elements outside the storey hierarchy

1,392 elements in the reference model have no storey: all 261 `IfcSpace` plus
1,036 unhosted `IfcFlowTerminal`. They still have geometry worth showing, so they
get the `unassigned` scene. `/api/model/elements` selects them by *absence* of a
storey name, since they have no name to match.

## Identifiers

Three identifiers coexist in the graph, and only one is usable as a viewer key:

| Property | Example | Role |
|---|---|---|
| `id` | `proj::model-abc::2JH4bkKkX2Aeig...` | graph primary key, composite for federated projects |
| `ifcGuid` | `2JH4bkKkX2AeigVl5N9K8X` | IFC GlobalId; **what glTF node names carry** |
| `tag` | `817660` | trailing numeric segment of `name`, what humans quote |

`scene_export` sets the serializer's `use-element-guids`, so every glTF node is
named by its IFC GlobalId. A highlight is therefore a direct node-name lookup
with no side index to keep in sync. When a query projects only `id`, the trailing
segment after `::` is used, because that segment *is* the GlobalId.

Traceability holds end to end: `IFC GlobalId -> nodes.csv ifc_guid -> Neo4j
ifcGuid -> visualization.harvest_elements -> visualization.highlight[].ifc_guid
-> glTF node name`.

## Coordinates and units

IfcOpenShell's glTF serializer emits Y-up, which is also three.js's convention,
so meshes load correctly oriented with no client-side rotation. The mapping from
the IFC/Neo4j frame, verified element-for-element against stored bounding boxes:

```text
gltf_x = ifc_x    gltf_y = ifc_z    gltf_z = -ifc_y
```

`ifc_bbox_to_gltf()` is the single implementation, used by both the API and the
camera framing, and pinned in `tests/test_scene_export.py` to values measured
from real exported geometry. Negating Y swaps which corner is minimal, so bounds
are re-derived rather than mapped corner to corner.

Geometry stays in **project file units** — feet for the reference model. Every
stored bounding box and clash metric is in file units, and the Results table
already reports "Metric (model units)". Rescaling to metres would desynchronise
the viewer from every number the platform reports.

## Deciding what to highlight

The core problem: the answer to "how many clashes are on Level 5?" is
`count(r)`, which names no element. Without a separate resolution step the viewer
would be empty for the most common question shape.

Identity is resolved on a second axis, in descending confidence:

1. **Planned identity query.** Each `GraphQueryPlan` may carry a
   `visualization_cypher`: a hand-written parameterized query that reuses the
   answer query's `MATCH`, `WHERE`, and parameters. Same filters, same parameter
   dict, so the highlighted set is provably the set the count was taken over.
2. **Identity columns already in the result.** Both shapes are recognised:
   explicit aliases (`element_a_guid`) from planners and templates, and unaliased
   projections (`e.ifcGuid`) from free-form LLM Cypher, whose columns Neo4j names
   after the expression text.
3. **Nothing.** The answer stands without a highlight, and the UI may offer the
   opt-in type view.

No language model is asked which elements matter. A model-authored element list
would be an unverified claim about the building — the class of output this
platform validates rather than trusts.

`GraphRetriever._resolve_elements()` never raises: the answer is the product, the
highlight is a presentation of it.

## Response contract

`POST /api/ask` attaches a `visualization` block to every answer, including
conversational ones, so the frontend never branches on the key's existence:

```json
{
  "available": true,
  "reason": "graph_elements",
  "project_id": "default-project",
  "file_ids": ["architectural", "mep"],
  "highlight": [
    {"element_id": "…", "ifc_guid": "…", "name": "…",
     "ifc_type": "IfcWall", "storey_name": "…"}
  ],
  "scenes": ["f1e481dbf2d9"],
  "related_types": [],
  "truncated": false
}
```

`reason` states why there is or is not something to show:

| `reason` | `available` | Meaning |
|---|---|---|
| `graph_elements` | `true` | Specific elements identified; the map auto-opens |
| `related_types` | `false` | No element evidence, but the subject maps to IFC types the user may opt into — orientation only |
| `no_evidence` | `false` | Nothing to show, nothing to offer |
| `graph_unavailable` | `false` | The graph was consulted and failed — deliberately distinct from finding nothing |

`highlight` is capped at 500 elements with `truncated` reporting the cut.

The block is deliberately **not** part of `sources`. `sources` drives citation
chips and is filtered against what the answer text actually cites; a geometry
highlight has a different lifecycle. When citation validation withholds an
answer, `highlight` is emptied too — highlighting elements would imply the system
verified a claim it just refused to make.

### The opt-in type view

A pure regulation answer ("minimum landing depth for stairs") verified no
elements. Highlighting every `IfcStair` would imply otherwise, so instead
`related_types` offers a clearly labelled button, and the viewer footer states
that what is shown is orientation and not evidence for the answer.

The vocabulary table in `bim_graph/visualization.py` is explicit and bilingual.
Terms are matched with non-word-character lookarounds rather than `\b`, because
`\b` is defined against `\w` and Persian letters are word characters — `\bدر\b`
still matches inside `چقدر`. ZWNJ is not a word character, so it acts as a
boundary and `پله` matches inside `راه‌پله`.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/model/manifest?project_id=` | Which scenes exist, their storey names, element counts, and sizes |
| `GET /api/model/scene/{project_id}/{file_id}/{scene_key}` | One storey's glTF binary |
| `GET /api/model/elements?project_id=&file_id=&scene_key=&ifc_type=` | Element identities and bounding boxes, already in the viewer's frame; repeat `file_id` to retain the selected model scope |

Every path segment of the scene route is normalised through `safe_id` and the
resolved path is asserted to remain inside `SCENES_ROOT`.

`/api/model/elements` returns each element's `scene_key`, so the client picks
scenes without reimplementing key derivation. It answers `503` when the graph is
unreachable, because an empty list would read as an empty building.

The answer payload records `file_ids`, and both the bounds query and manifest
selection apply that scope. This matters for earlier chat turns: changing the
Pipeline model selection later does not change which geometry an old answer opens.

## Frontend

`static/viewer.js` is an ES module; `static/app.js` remains a classic script and
drives it through `window.bimViewer`.

- **One WebGL context** for the whole app, re-targeted per answer. Browsers cap
  concurrent contexts at roughly 8–16, so a context per message would degrade a
  long conversation.
- **A monotonic load token** means a slow fetch for a superseded answer cannot
  overwrite the scene the user is looking at, and loading happens *before*
  clearing, so a failing fetch does not blank the current view.
- **A bounded LRU of 6 parsed scenes**, disposing geometry on eviction. Unbounded
  caching would eventually hold the whole 88 MB building in GPU memory.
- **At most 4 scenes per answer**, smallest first, with the footer reporting when
  the cap applied.
- **Normals are computed on load.** The exporter writes `POSITION` only; without
  this every surface renders unlit.
- **Highlights are matched up the ancestor chain.** `GLTFLoader` only names a Mesh
  after its glTF node when the mesh has one primitive; multi-primitive elements
  (42 of 236 on one reference storey) become a Group named by GUID whose child
  meshes carry IFC step ids. Matching only the mesh name would miss them.
- **Distinct elements are counted, not meshes**, because that is what the UI
  reports.

three.js r180 is vendored under `static/vendor/three/` (~900 KB, four files) and
resolved through an import map, so the viewer needs no bundler and works offline —
which matters for the Compose client deployment.

### Fallback

A project with graph data but no exported scenes renders bounding boxes instead,
labelled as such. That is honest: boxes are exactly what the graph stores, and
what the clash engine measured.

## Storage lifecycle

```text
dataset/ifc/scenes/<project_id>/manifest.json
dataset/ifc/scenes/<project_id>/<file_id>/<scene_key>.glb
```

- The manifest is merged **per file**, so ingesting one discipline model does not
  invalidate its siblings' scenes.
- Writes are atomic (temp file plus `os.replace`) under a re-entrant lock.
- Re-ingestion prunes files no longer in the graph, deleting both the manifest
  entry and the geometry: a viewer that can still load a model the answers cannot
  reason about is worse than one reporting the geometry as missing.
- A scene is published only if its size matches the length declared in its glTF
  header. The IfcOpenShell serializer flushes its final buffer from a C++
  destructor, so a file measured while any Python reference is alive is short —
  observed as 8,991,664 bytes against a correct 8,998,268 on a 571-element scene.
  `SceneWriter.close()` releases each serializer, then verifies.
- Per-element write failures are counted in `failed_elements` rather than failing
  ingestion. The graph is the authoritative product; the viewer presents it.

`dataset/ifc/scenes/` is excluded from Git and from the Docker image, and is
mounted at runtime through the existing `./dataset` volume.

## Verification

| Check | Status |
|---|---|
| IFC → glTF export, whole reference model | 45 scenes, 88.2 MB, 0 failures |
| glTF node names are graph GlobalIds | Verified, all scenes |
| Axis mapping vs. stored bounding boxes | Verified on 121 elements, exact |
| Manifest size vs. file vs. glTF header | Consistent |
| Scene-route path traversal | 5 attack shapes rejected |
| Repository IFC → glTF export | Passed with 155 walls on `T.O. Landing05` |
| Headless Chromium WebGL load/render | Passed with the exported 154,504-byte GLB |
| Test suite | `tests/test_scene_export.py`, `test_visualization_scope.py`, `test_model_scene_api.py`, `test_answer_visualization.py` |

Neo4j-independent graph-backed paths are covered with fakes faithful to the driver's behaviour —
notably that `Neo4jClient.run()` calls `Record.data()`, which converts `Node`
objects to plain dicts and discards labels. The integration environment also
executed a headless Chromium/WebGL smoke test against a real scene generated from
the repository's reference IFC; interactive orbit controls were not manually tested.

## Limitations

- Bounding-box fidelity in the fallback path is exactly the AABB precision of the
  clash engine — no exact intersection solids, consistent with the clash
  documentation.
- Cross-file federation shows each model's own scenes; alignment is validated at
  ingestion by `bim_graph/coordinate_system.py`, not re-checked here.
- The scene endpoints have no authentication, consistent with every other
  endpoint in this application. See the README's production-readiness section.
