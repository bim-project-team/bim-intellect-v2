# BIM-Intellect 3D Visualization — Current Issue and Chat Test Questions

## Purpose

This document summarizes:

1. the current 3D Visualization problem observed in the BIM-Intellect chat workflow;
2. the evidence showing that exported 3D geometry already exists;
3. the most likely failure point in the current visualization flow; and
4. practical Chat questions, based on the actual Clash CSV, for validating 3D element highlighting.

---

## 1. Clash CSV Summary

Source file:

`clash-results-clashes-2026-09-19.csv`

The CSV contains:

- **Total clash rows:** 43
- **CROSS-FILE clashes:** 38
- **INTRA-FILE clashes:** 5
- **Issue type:** CLASH
- **Discipline values:** currently `unspecified` in the exported rows

The CSV includes the fields required for precise graph-to-viewer testing:

- Element A/B IFC Type
- Element A/B Name
- Element A/B composite Element ID
- Element A/B IFC GUID
- Element A/B source IFC file
- Element A/B discipline
- Relation (`CROSS-FILE` / `INTRA-FILE`)
- Issue
- Metric

---

## 2. Current 3D Visualization Problem

When a clash-related question is asked in Chat, the viewer currently displays:

> **Showing bounding boxes: this project has no exported 3D geometry.**

However, this message is misleading for the current project.

The browser network trace shows the viewer is requesting:

```text
GET /api/model/elements?project_id=default-project&scene_key=unassigned&file_id=model_0_structure-554019768fa9&file_id=model_0_arc-b4ec74d6f99b
```

The important detail is:

```text
scene_key=unassigned
```

The project ID and file IDs are correct, but the viewer is resolving the highlighted clash elements to `unassigned` instead of to one of the actual exported storey scenes.

---

## 3. Evidence That Real 3D Geometry Already Exists

The current `manifest.json` proves that exported GLB geometry exists for the same project and files.

### Project

```text
project_id = default-project
```

### Structure model

```text
file_id = model_0_structure-554019768fa9
source_ifc_file = model_0_structure.ifc
geometry_unit = metre
```

Exported scene:

```text
Storey
scene_key = 06d3131f397d
element_count = 149
```

### Architecture model

```text
file_id = model_0_arc-b4ec74d6f99b
source_ifc_file = model_0_arc.ifc
geometry_unit = metre
```

Exported scenes exist for:

- Ebene 1
- Ebene 2
- Ebene 3
- Ebene 4
- Ebene 5
- Ebene 6
- Ebene 7
- Ebene 8
- Ebene 9

### Manifest totals

- **Exported GLB scenes:** 10
- **Elements represented in exported scenes:** 499
- **Failed geometry exports:** 0

Therefore, the problem is **not that the project has no 3D geometry**.

---

## 4. Most Likely Root Cause

The current failure is most likely in the element-to-scene mapping stage:

```text
Chat / Graph result
        ↓
Highlighted IFC element
        ↓
storey_name
        ↓
scene_key
        ↓
Manifest lookup
        ↓
GLB scene
        ↓
IFC GUID highlight
```

The current request shows that this chain is resolving to:

```text
scene_key = unassigned
```

even though the manifest contains valid storey scenes.

Possible causes include:

1. `storey_name` is missing, empty, or null in the Chat visualization payload.
2. The graph identity query returns IFC GUID/name/type but does not return the storey.
3. The Results/Graph data contains the clash element but its `storeyName` was not resolved.
4. The frontend falls back to `unassigned` when storey information is missing.
5. The viewer is deriving a scene key before checking the real manifest scene mapping.
6. A clash element belongs to a real exported scene, but the mapping from graph element → storey → scene is not being performed correctly.

The exporter, file scope, and manifest itself appear to be working.

---

## 5. Expected Correct Behavior

For an architecture element on `Ebene 5`, the flow should be:

```text
IFC GUID
   ↓
storey_name = Ebene 5
   ↓
scene_key = 8ad523aa4c9d
   ↓
GET /api/model/scene/default-project/model_0_arc-b4ec74d6f99b/8ad523aa4c9d
   ↓
Load GLB
   ↓
Find node by IFC GUID
   ↓
Highlight exact element
```

For the structure model:

```text
storey_name = Storey
   ↓
scene_key = 06d3131f397d
```

The `unassigned` fallback should only be used for elements that genuinely have no storey and have a corresponding exported `unassigned` scene.

---

## 6. Recommended Viewer Error Messages

The current message:

> `this project has no exported 3D geometry`

should only be used when the manifest truly contains no scenes.

The viewer should distinguish between:

### No geometry exported

```text
No exported 3D geometry exists for this project.
```

### Geometry exists but scene mapping failed

```text
Exported 3D geometry exists, but no matching scene could be resolved for the selected elements.
```

### Scene exists but element is not found

```text
The 3D scene was loaded, but the requested IFC element could not be matched by GUID.
```

This makes debugging and client-facing behavior much clearer.

---

# 7. Chat Questions for 3D Visualization Testing

The following questions are based directly on the actual Clash CSV.

## Test 1 — Highest-metric clash

Ask:

> **Find the clash with the highest metric in the current project. Return the names, IFC types, Element IDs, IFC GUIDs, source files, relation type, and metric for both elements, then highlight those exact elements in the 3D Visualization.**

Expected target:

### Element A

- Type: `IfcSlab`
- Name: `Basic Roof:Titanzink - Eindeckung:150177`
- IFC GUID: `1$DZROIQX0RgNTaHr7NfS$`
- Source: `model_0_arc.ifc`

### Element B

- Type: `IfcSlab`
- Name: `Slab 0.1500`
- IFC GUID: `39hMvtcAHFA8CweEUbIYMM`
- Source: `model_0_structure.ifc`

### Expected relation

- Relation: `CROSS-FILE`
- Metric: `8.3768`

This is the best end-to-end test because it checks:

```text
Neo4j retrieval
→ clash provenance
→ IFC GUID resolution
→ storey/scene selection
→ GLB loading
→ 3D highlighting
```

---

## Test 2 — Exact GUID-to-GUID clash

Ask:

> **Find the cross-file clash between IFC GUID `1$DZROIQX0RgNTaHr7NfS$` and IFC GUID `39hMvtcAHFA8CweEUbIYMM`. Show both exact elements in the 3D Visualization.**

This is useful because the viewer should not need to infer which elements are intended.

---

## Test 3 — Slab-to-beam cross-file clash

Ask:

> **Show the clash between IFC GUID `1$DZROIQX0RgNTaHr7NfS$` and IFC GUID `1ZT6ZjeV96FhwNo0GJF_M6`, and highlight both elements in the 3D Visualization.**

Expected pair:

- `IfcSlab` — `Basic Roof:Titanzink - Eindeckung:150177`
- `IfcBeam` — `4th floor beams-300x600`
- Relation: `CROSS-FILE`
- Metric: `0.4048`

---

## Test 4 — Real intra-file clash

Ask:

> **Find the intra-file clash between IFC GUID `1$DZROIQX0RgNTaHr7NfS$` and IFC GUID `058byadMj8QOKMXiyfDP7a` in `model_0_arc.ifc` and highlight both elements in 3D.**

Expected:

- Element A: `IfcSlab` — `Basic Roof:Titanzink - Eindeckung:150177`
- Element B: `IfcWall` — `Basic Wall:DPW 24.0:191852`
- Relation: `INTRA-FILE`
- Metric: `0.1832`

This verifies that visualization is not limited to cross-file clashes.

---

## Test 5 — Cross-file architecture/structure query

Ask:

> **Find one cross-file clash between `model_0_arc.ifc` and `model_0_structure.ifc`. Return both IFC GUIDs and highlight the exact elements in the 3D Visualization.**

Expected behavior:

- one element should come from `model_0_arc.ifc`;
- one element should come from `model_0_structure.ifc`;
- both should be mapped to their own exported scenes;
- both should be highlighted.

---

## Test 6 — Top-N clash visualization

Ask:

> **Show the top 5 highest-metric cross-file clashes between `model_0_arc.ifc` and `model_0_structure.ifc`, and highlight all involved elements in the 3D Visualization.**

This tests:

- multiple graph result rows;
- duplicate GUID handling;
- multiple scenes;
- viewer highlight limits;
- scene aggregation.

---

## Test 7 — Type-specific visualization

Ask:

> **Find the highest-metric clash between an `IfcSlab` and an `IfcBeam`, return both GUIDs and source files, and highlight both elements in 3D.**

This tests graph filtering plus viewer identity resolution.

---

## Test 8 — Compare intra-file and cross-file

Ask:

> **Find one INTRA-FILE clash and one CROSS-FILE clash. Show the provenance for both pairs and highlight all involved elements in the 3D Visualization.**

This tests whether relation type changes have any unintended effect on visualization.

---

# 8. What to Check in `/api/ask`

For every test above, inspect the response from:

```text
POST /api/ask
```

The response should contain a visualization block similar to:

```json
{
  "visualization": {
    "available": true,
    "reason": "graph_elements",
    "project_id": "default-project",
    "file_ids": [
      "model_0_structure-554019768fa9",
      "model_0_arc-b4ec74d6f99b"
    ],
    "highlight": [
      {
        "element_id": "...",
        "ifc_guid": "...",
        "name": "...",
        "ifc_type": "...",
        "storey_name": "..."
      }
    ]
  }
}
```

Pay particular attention to:

```text
storey_name
```

If it is null, empty, or missing for clash elements that actually belong to an exported storey, that strongly supports the current suspected root cause.

---

# 9. What to Check in the Browser Network Tab

For a successful visualization, the browser should eventually request actual GLB scenes such as:

```text
GET /api/model/scene/default-project/model_0_structure-554019768fa9/06d3131f397d
```

or an architecture scene such as:

```text
GET /api/model/scene/default-project/model_0_arc-b4ec74d6f99b/8ad523aa4c9d
```

A request only to:

```text
/api/model/elements?...&scene_key=unassigned
```

followed by bounding-box fallback indicates that the scene resolution step failed.

---

# 10. Current Diagnosis

Based on the current evidence:

### Confirmed working

- IFC ingestion
- geometry extraction
- project/file IDs
- GLB generation
- GLB manifest generation
- architecture and structure scene storage
- clash provenance
- project/file scoping

### Current failure

The viewer is requesting:

```text
scene_key=unassigned
```

instead of mapping the graph-highlighted element to the correct exported storey scene.

### Most likely component to investigate

```text
Graph/Chat visualization payload
→ element storey resolution
→ scene key resolution
→ manifest scene lookup
```

The 3D exporter itself is not currently the primary suspect.

---

# 11. Acceptance Criteria for the Fix

The issue should be considered fixed when:

1. a clash question returns the expected IFC GUIDs;
2. each highlighted element has the correct project/file scope;
3. each element resolves to its real storey where applicable;
4. the viewer selects a scene that actually exists in `manifest.json`;
5. the corresponding GLB endpoint is requested;
6. the GLB loads successfully;
7. the IFC GUID is found in the scene;
8. the requested clash elements are visibly highlighted;
9. bounding-box fallback is only used when real scene geometry genuinely cannot be used;
10. the UI no longer claims that the project has no 3D geometry when scenes are present.

