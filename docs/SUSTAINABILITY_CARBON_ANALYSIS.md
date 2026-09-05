# Sustainability Carbon Analysis

**Implementation:** Phase 1  
**Methodology version:** 1.0  
**Extractor version:** 1.0  
**Updated:** 2026-09-04

## Scope

BIM-Intellect now provides deterministic, project/file-scoped embodied-carbon estimates from IFC material and quantity evidence. The feature is additive to the current graph and does not change `Element`, clash, clearance, or anomaly semantics.

It is not an LCA, EPD generator, energy simulation, environmental certification, or LEED certification engine. Results are only as applicable and accurate as the selected IFC evidence and operator-supplied carbon-factor dataset.

## Execution flow

```text
registered and ingested IFC files
  -> graph-retained semantic Element IDs/GUIDs
  -> IFC material and quantity extraction
  -> deterministic material normalization and exact alias lookup
  -> unit normalization and dimensional validation
  -> quantity × compatible carbon factor
  -> versioned Neo4j sustainability run/results
  -> project/file/material/type/element summaries
```

Only semantic elements already retained in Neo4j are evaluated. This preserves the project, file, storey, and IFC-type filtering used during graph ingestion.

## IFC material extraction

The extractor traverses occurrence material relationships first and falls back to the related IFC type only when no usable occurrence assignment exists. This prevents double counting an inherited type assignment that an occurrence overrides.

Supported structures are:

- `IfcRelAssociatesMaterial`;
- direct `IfcMaterial`;
- `IfcMaterialLayer`, `IfcMaterialLayerSet`, and `IfcMaterialLayerSetUsage`;
- `IfcMaterialProfile`, `IfcMaterialProfileSet`, and their usage wrappers;
- `IfcMaterialConstituent` and `IfcMaterialConstituentSet`;
- IFC2X3 `IfcMaterialList`, which occurs in the repository sample files.

For every material use, the system retains the authored `raw_name`, a separate deterministic `normalized_name`, category, association class/level, component type/index, IFC entity ID, layer thickness or constituent fraction when available, and extractor version. Raw IFC text is never overwritten.

Material-use IDs include the project-scoped `Element.id`, association/component identity, material identity, and extractor version.

## IFC quantity extraction

`IfcRelDefinesByProperties` relationships are traversed to `IfcElementQuantity`. Nested `IfcPhysicalComplexQuantity` members are recursively visited. Supported values are:

| IFC class | Dimension | Raw value field | SI base |
|---|---|---|---|
| `IfcQuantityWeight` | mass | `WeightValue` | kg |
| `IfcQuantityVolume` | volume | `VolumeValue` | m3 |
| `IfcQuantityArea` | area | `AreaValue` | m2 |
| `IfcQuantityLength` | length | `LengthValue` | m |

The record retains:

```text
quantity_value
quantity_type
quantity_unit
quantity_source
normalized_value
normalized_unit
quantity_name
quantity_set_name
association_level
derivation_method
quality
error
```

`quantity_source` is one of:

- `ifc_explicit` — a value authored in an `IfcElementQuantity`;
- `geometry_derived` — a derived AABB envelope estimate;
- `unavailable` — no supported usable value.

An invalid explicit quantity remains stored as `ifc_explicit` with its raw value and an error; it is not used for calculation.

### Quantity selection

Selection is deterministic. Explicit quantities rank before geometry estimates. Within a dimension, reviewed names rank in this order:

```text
mass:   NetWeight, Weight, GrossWeight
volume: NetVolume, Volume, GrossVolume
area:   NetArea, Area, NetSurfaceArea, NetSideArea, GrossArea,
        GrossSurfaceArea, GrossFootprintArea, GSA BIM Area
length: Length, Height, Width, Depth, Perimeter
```

If equally ranked records contain different normalized values, the status is `ambiguous_quantity` and no calculation occurs.

### IFC unit plausibility guard

Some exporters write raw area/volume magnitudes in powers of the project length unit while declaring a different project area/volume unit. The extractor does not silently repair or reinterpret this evidence.

When bounds exist, a deliberately generous 100× envelope comparison detects gross inconsistencies:

```text
volume_limit = bbox_dx × bbox_dy × bbox_dz
area_limit   = 2 × (bbox_dx×bbox_dy + bbox_dx×bbox_dz + bbox_dy×bbox_dz)
length_limit = 4 × (bbox_dx + bbox_dy + bbox_dz)
```

An explicit normalized value greater than 100 times the applicable limit is retained with an error and excluded. This is only a gross unit/export sanity check; passing it does not prove the quantity is correct.

## Derived geometry estimates

The API default is:

```json
{"allow_geometry_derived": false}
```

When explicitly enabled and a compatible explicit dimension is unavailable, the system may derive:

```text
volume = dx × dy × dz
area   = max(dx × dy, dx × dz, dy × dz)
length = max(dx, dy, dz)
```

Only configured IFC types are eligible. The result is always marked `geometry_derived`, `quality=low`, and `calculated_estimate` if used.

Graph bounds come from the existing IfcOpenShell geometry configuration with world coordinates and `CONVERT_BACK_UNITS=False`; their unit contract is metres. Material layer thickness and explicit IFC quantities follow their own IFC project/local units and are normalized separately.

AABB values are envelopes. They can overstate rotated, hollow, curved, tapered, composite, or irregular elements and must not be described as exact material quantities.

## Unit normalization

Canonical internal units are:

```text
mass   -> kg
volume -> m3
area   -> m2
length -> m
carbon -> kgCO2e
```

An explicit quantity-level IFC unit overrides the project default. SI prefixes and conversion-based units are resolved to their canonical dimension. Textual conversion helpers support common metre/millimetre/foot, square-unit, cubic-unit, gram/kilogram, and pound representations.

Supported carbon-factor denominator dimensions are:

```text
kgCO2e/kg -> mass
kgCO2e/m3 -> volume
kgCO2e/m2 -> area
```

The calculator rejects mismatches. It never applies density or another implicit conversion to make incompatible values multiply.

## Carbon-factor repository

The configured default is:

```text
SUSTAINABILITY_CARBON_FACTORS=dataset/sustainability/carbon_factors.csv
```

The checked-in file is a header-only template. It contains no production carbon values. See `dataset/sustainability/README.md` and `carbon_factors.schema.json`.

Required CSV fields:

```text
factor_id, material_id, material_name, aliases, category,
factor_value, factor_unit, region, source, source_version,
year, dataset_version, notes, enabled
```

The repository calculates a SHA-256 hash over the complete source file. Missing provenance/identity, invalid or non-finite units/values/years, and duplicate factor IDs fail loading. Only `enabled=true` factors participate in matching. A persisted factor-node ID is derived from `dataset hash + logical factor_id`, so replacing the configured CSV cannot rewrite the factor record linked from an older run.

An analysis automatically selects a factor dataset version or region only when exactly one enabled choice exists. When a factor file contains multiple enabled dataset versions or multiple regions within the selected version, the caller must select one explicitly. This prevents a project total from silently mixing factor editions or regions.

`source`, `source_version`, `region`, `year`, `dataset_version`, and `notes` must describe applicability and lifecycle boundary. The software validates presence and structure; it does not certify that a supplied dataset is environmentally authoritative.

## Material matching

Matching is deterministic and uses normalized exact keys from:

- `material_id`;
- canonical `material_name`;
- pipe-separated reviewed `aliases`.

Region, dataset version, and available quantity dimension further restrict candidates. Category is descriptive and is not an automatic fallback. Fuzzy matching and LLM mapping are not used for calculation.

Statuses are:

- `matched` — exactly one enabled applicable factor;
- `ambiguous` — more than one applicable factor;
- `unmatched` — no applicable exact key.

For example, the three names below resolve together only if the factor row explicitly lists the variants as aliases:

```text
Concrete
Concrete - Cast in Situ
Cast-In-Place Concrete
```

## Calculation formula and allocation

For compatible canonical units:

```text
estimated_embodied_carbon_kgCO2e
    = normalized_quantity × factor_value
```

Examples of dimensionally valid multiplication are kg × kgCO2e/kg, m3 × kgCO2e/m3, and m2 × kgCO2e/m2. Python `Decimal` is used for multiplication; the stored result is serialized as a floating-point API/Neo4j value.

A single material receives the selected whole-element quantity. For multiple materials:

- volume may be allocated across layers by positive layer-thickness ratio;
- a quantity may be allocated across constituents by positive authored fraction;
- IFC material lists, profiles without usable shares, or other unresolved compositions return `missing_quantity` rather than assuming equal shares.

Allocated values are `calculated_estimate`, even when their base whole-element quantity was explicit IFC evidence.

Calculation statuses include:

```text
calculated_explicit
calculated_estimate
missing_material
unmatched_material
ambiguous_mapping
missing_quantity
ambiguous_quantity
incompatible_unit
```

Missing values contribute no carbon number. They are not converted to zero.

## Stored provenance

The additive graph model is:

```text
(:BIMProject)-[:HAS_SUSTAINABILITY_RUN]->(:SustainabilityRun)
(:SustainabilityRun)-[:ANALYZED_MODEL]->(:IFCModel)
(:SustainabilityRun)-[:HAS_RESULT]->(:SustainabilityResult)
(:SustainabilityResult)-[:FOR_ELEMENT]->(:Element)
(:Element)-[:HAS_MATERIAL_USE]->(:MaterialUse)-[:OF_MATERIAL]->(:Material)
(:Element)-[:HAS_QUANTITY_EVIDENCE]->(:QuantityEvidence)
(:SustainabilityResult)-[:FOR_MATERIAL_USE]->(:MaterialUse)
(:SustainabilityResult)-[:USES_QUANTITY]->(:QuantityEvidence)
(:SustainabilityResult)-[:USES_FACTOR]->(:CarbonFactor)
```

Every result retains:

- `Element.id`, original IFC GUID, IFC type, name, and storey;
- `projectId`, `sourceFileId`, source IFC filename, and discipline;
- raw and normalized material;
- raw and normalized quantity, dimension, unit, source, exact evidence ID, and allocation share;
- logical and content-versioned factor IDs, value/unit/source/source version, region, year, dataset version, and dataset hash;
- calculated `kgCO2e`, status, allocation note, run ID, and methodology version.

A new successful run becomes current only after result batches are persisted. Repeating a scope creates another run and makes the earlier one non-current. Re-ingesting/replacing a project removes its dependent sustainability runs, results, material uses, and quantity evidence before deleting graph elements. Global factor nodes and materials used by other projects are not part of clash/anomaly queries.

## Aggregation

Summaries include:

- project total for calculated rows;
- explicit and estimated carbon subtotals;
- total evaluated elements and elements with calculated carbon;
- elements missing material or quantity;
- unmatched material count and ambiguous mapping count;
- explicit versus estimated result counts;
- element coverage ratio;
- status counts;
- carbon, result/element counts, element coverage, and status counts grouped by project, source IFC file, discipline, IFC type, normalized material, and element.

Every grouping includes unknown/unmatched rows in its counts and coverage while summing only calculated `kgCO2e`. Excluded rows therefore remain visible but do not alter the numeric total.

## API

All routes are under `/api/sustainability`.

### Run analysis

```http
POST /api/sustainability/analyze
Content-Type: application/json
```

```json
{
  "project_id": "default-project",
  "file_ids": ["architecture-abcdef123456"],
  "factor_dataset_version": "approved-v1",
  "region": "target-region",
  "allow_geometry_derived": false
}
```

Omitting `file_ids` selects all currently ingested files in the named project. Unknown, non-ingested, empty, or federation-incompatible scopes fail rather than falling back to another project. `factor_dataset_version` and `region` may be omitted only when their enabled choice is unique (or the factor template is empty).

### Retrieve summary

```http
GET /api/sustainability/summary?project_id=default-project&file_id=architecture-abcdef123456
```

Optional `run_id` selects a historical run within the same exact project/file scope.

### Retrieve material groups

```http
GET /api/sustainability/materials?project_id=default-project&file_id=architecture-abcdef123456
```

### Retrieve element/material-use results

```http
GET /api/sustainability/elements?project_id=default-project&file_id=architecture-abcdef123456&status=unmatched_material&limit=100&offset=0
```

Optional filters are `run_id`, `status`, and exact `ifc_type`. The maximum page size is 1,000.

### Inspect factors

```http
GET /api/sustainability/factors?dataset_version=approved-v1&region=target-region
```

This returns the dataset path/hash/version/count and factor provenance. It is read-only.

### Error shape

Scope and data errors use a stable detail object:

```json
{
  "detail": {
    "code": "analysis_not_found",
    "message": "No completed sustainability analysis exists for the requested scope."
  }
}
```

## Example output

The following is a **synthetic arithmetic example only**, using a test-only factor of `100 kgCO2e/m3`; it is not an environmental claim:

```json
{
  "project_id": "test-project",
  "file_ids": ["architecture-test"],
  "total_evaluated_elements": 1,
  "elements_with_calculated_carbon": 1,
  "explicit_quantity_results": 1,
  "estimated_quantity_results": 0,
  "calculated_carbon_kgco2e": 250.0,
  "explicit_carbon_kgco2e": 250.0,
  "estimated_carbon_kgco2e": 0.0,
  "element_coverage_ratio": 1.0,
  "calculation_status_counts": {
    "calculated_explicit": 1
  }
}
```

The reproducible input is `2.5 m3 × 100 kgCO2e/m3 = 250 kgCO2e`.

With the repository's default header-only factor file, analysis can still extract and persist material/quantity evidence, but material results are unmatched and the calculated carbon total is zero with zero calculated coverage. That zero means “no calculated rows,” not zero environmental impact.

## Assumptions and limitations

- IFC material assignments describe modeled/authored information, not necessarily procured products.
- Explicit IFC quantities can be missing, duplicated, semantically unsuitable, or exporter-inconsistent.
- The plausibility guard detects only gross inconsistencies and does not validate engineering truth.
- AABB volume/area/length is a low-quality envelope estimate and is disabled by default.
- Layer thickness allocation assumes uniform volume distribution; constituent allocation trusts authored fractions.
- No density-derived mass is implemented in Phase 1.
- No lifecycle-stage harmonization is inferred. Operators must use factors with a consistent documented boundary.
- Factor aliases require curation. Unmatched/ambiguous values are intentionally not calculated.
- Totals are partial whenever element coverage is less than 100%.
- Analysis is synchronous and protected by the current process-level pipeline workflow; large files may take time.
- Project re-ingestion invalidates/removes project-owned sustainability results because their source element graph changed.
- The language model is not used for material matching, unit conversion, arithmetic, or factor selection.
- Phases 2 and 3 add LEED-oriented retrieval, grounded sustainability chat routing, the project/file-scoped dashboard, and JSON/CSV/HTML report assembly. They do not implement certification scoring, a full LEED engine, or authoritative factors where none have been supplied.

## Verification

On 2026-09-04, `tests/test_sustainability_phase1.py` passed 18 tests and the complete repository suite passed 102 tests with 3 optional/environment-specific skips. Compilation, frontend JavaScript syntax, factor-schema parsing, OpenAPI generation, `GET /` smoke serving, and `docker-compose.client.yml` configuration also passed. A live Neo4j server was unavailable locally; Neo4j persistence, current-run, cleanup, and provenance behavior were therefore exercised with isolated repository/query-contract tests, not claimed as a live database integration result.
