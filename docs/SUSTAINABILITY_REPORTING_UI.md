# Sustainability Dashboard and Reports

**Implementation:** Phase 3  
**Updated:** 2026-09-05

## Scope

Phase 3 completes the operator-facing sustainability workflow without changing the existing Chat, Pipeline, Results, or Documents behavior. Sustainability is a fifth sibling workspace view. It consumes the deterministic Phase 1 APIs and the grounded Phase 2 LEED assessment output; it does not calculate carbon in JavaScript and does not infer certification.

## Dashboard scope and state

The active project ID remains the Pipeline project field. The dashboard offers two explicit IFC scopes:

- selected IFC files, using the existing multi-file selection;
- all ingested IFC files registered to the active project.

Every request sends the project ID and, for selected scope, repeated file IDs. The frontend clears prior content before loading, compares the returned project and selected file IDs to the current scope, and ignores stale responses with a request token. Consequently, a slow response from a previous project cannot replace the current dashboard.

The visible states are loading, success, partial data, no exact-scope analysis, invalid/empty scope, and backend error. A partial result is not displayed as failure: its excluded and estimated records are exposed in Data quality.

## Project summary and breakdowns

The six summary cards show:

- estimated embodied carbon, only when at least one element has calculated carbon;
- elements with calculated carbon;
- elements not evaluated;
- explicit-quantity element coverage;
- estimated-quantity element coverage;
- unmatched material count.

Carbon breakdowns are switchable among material, IFC type, discipline, and source IFC file. CSS-native bars use only stored calculated kgCO2e. Zero/unknown exclusions do not appear as zero-carbon contributors.

Top contributors show the element name, IFC type, GUID, source IFC file, raw material, normalized quantity/unit, factor/unit/source, calculation provenance badge, and kgCO2e. Derived quantities and allocation estimates remain visibly marked Estimated.

## Data quality

The dashboard distinguishes:

    missing_material
    missing_quantity
    ambiguous_quantity
    ambiguous_mapping
    unmatched_material / missing carbon factor
    incompatible_unit
    calculated_estimate

The detail table preserves IFC GUID, source file, authored material name, machine status, and calculation note. Unknown records are never rendered as zero kgCO2e.

## LEED-oriented findings

When Chat performs a hybrid LEED assessment, the orchestrator records the already citation-validated result in the bounded conversation store. The record contains:

- the user's criterion/question;
- conservative assessment status;
- grounded answer text;
- exact project/file scope and sustainability run;
- retrieved document citations;
- available missing-project-data categories;
- timestamp and a session-only marker.

The Sustainability view loads only findings matching the current conversation, project, and exact file set. These snapshots are presentation/report evidence, not Neo4j carbon inputs or persistent LEED credit decisions. Restarting the application or expiring the conversation removes them.

## Report API

    GET /api/sustainability/report
        ?project_id=<project>
        &file_id=<repeatable-file-id>
        &run_id=<optional-run>
        &conversation_id=<optional-chat>
        &format=json|csv|html

All formats are downloads. JSON is the canonical machine-readable form with schema version sustainability-report-v1. CSV is a flat audit stream containing metadata, summaries, breakdowns, contributors, factor citations, quality counts, LEED-oriented findings, citations, and limitations. HTML is a standalone printable report.

The report includes:

- project and selected IFC models;
- analysis/run date and methodology;
- factor dataset version, hash, region, and used-factor provenance;
- total estimated kgCO2e and coverage;
- all four breakdowns and top contributors;
- missing, unmatched, incompatible, and estimated records;
- conversation-scoped LEED-oriented findings and document citations;
- explicit environmental and certification limitations.

The API performs no recalculation. It assembles the current or requested persisted run and its stored result rows.

## Document management

Documents now exposes document_domain, standard_name, and standard_version inputs. Existing uploads default to regulation. LEED and generic standard uploads require a version, matching the backend classification rules. Indexed rows show their stored domain and standard/version.

## Accessibility and localization

All operator labels are available in English and Persian. Dynamic IFC names, project IDs, questions, and evidence use automatic text direction. Numeric quantities, factor units, GUIDs, and citations retain stable machine-readable presentation. Tables remain horizontally scrollable at narrow widths.

## Limitations

- The dashboard cannot make missing or unapproved carbon factors authoritative.
- LEED findings exist only after a grounded assessment question and remain session-local.
- No reviewed general LEED credit evaluator, points roll-up, or certification engine exists.
- HTML reports are printable, not digitally signed.
- Very large reports are assembled synchronously and should move to a background artifact service for multi-user production deployments.
