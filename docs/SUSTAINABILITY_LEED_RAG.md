# Sustainability + LEED Retrieval and Hybrid Reasoning

**Implementation:** Phase 2  
**Updated:** 2026-09-05

## Scope and safety boundary

Phase 2 adds classified sustainability/LEED document retrieval and a separate deterministic sustainability evidence source to the existing conversation-aware RAG orchestrator.

```text
LEED / standards PDFs                  Project BIM/carbon results
        |                                        |
clause/page-aware Chroma RAG          SustainabilityService + Neo4j
        |                                        |
        +------------ grounded orchestrator -----+
                              |
                    explanation, not calculation
```

Document text supplies requirements and guidance. The sustainability engine supplies project quantities, factors, carbon totals, contributors, coverage, and exclusions. Neither source may substitute for the other, and the final language model is not used for carbon arithmetic, factor selection, or material matching.

This is a LEED-oriented evidence workflow, not a certification, credit-scoring, energy-simulation, or LCA engine.

## Document classification and ingestion

Every newly ingested chunk carries scalar Chroma metadata:

```text
document_domain = regulation | sustainability | leed | standard
standard_name
standard_version
```

Existing ingestion remains backward compatible: omitted classification defaults to `regulation`, and pre-Phase-2 chunks without `document_domain` are also interpreted as `regulation`. LEED defaults `standard_name` to `LEED` but requires an explicit version. A generic `standard` requires both name and version.

Upload example:

```http
POST /api/rag/upload?document_domain=leed&standard_version=v4.1
Content-Type: multipart/form-data
```

Server-path ingestion uses the same query parameters:

```http
POST /api/rag/ingest?pdf_path=docs/leed.pdf&document_domain=leed&standard_version=v4.1
```

The indexer CLI can classify a whole source directory:

```powershell
python -m rag.indexer --source-dir dataset/leed `
  --document-domain leed --standard-name LEED --standard-version v4.1
```

Classification is explicit metadata, not a filename or LLM guess. The current collection remains schema-compatible: ordinary unfiltered regulation retrieval continues to see legacy chunks, while sustainability-only filters select classified `leed`, `sustainability`, and `standard` chunks.

## Retrieval isolation

Domain filters are applied to dense Chroma retrieval, lexical fallback, reranking candidates, final candidate selection, and diagnostics. Section completion and neighbor expansion start from an already filtered document/section and cannot cross into another document.

| Question | Document domains |
|---|---|
| Ordinary code/regulation question | `regulation` or backward-compatible unfiltered corpus |
| LEED requirement | `leed`, `sustainability`, `standard` |
| General sustainable-material guidance | `leed`, `sustainability`, `standard` |
| Explicit cross-domain question | the explicitly requested union |

No LEED document found means no LEED requirement is supplied to the answer model. The system does not fill that gap from model knowledge.

## Sustainability evidence retrieval

`sustainability/retriever.py` calls the public deterministic service methods for the exact `project_id` and optional selected `file_ids`. The chat request accepts:

```json
{
  "question": "Which material has the largest carbon contribution?",
  "project_id": "project-a",
  "file_ids": ["architecture-abcdef123456"],
  "use_strong_models": false
}
```

The existing browser chat automatically forwards the active project and selected IFC file IDs. If no project scope is available, the retriever returns `project_scope_required`; it never searches another project or combines projects.

The bounded evidence object contains the stored run ID, scope, methodology and factor dataset provenance, totals, coverage, status counts, and relevant material/file/type/element detail. It is serialized inside a marked `SUSTAINABILITY_EVIDENCE` block with an explicit instruction not to recalculate.

Supported semantic evidence intents are:

```text
summary
top_materials
by_file
top_elements
missing_evidence
leed_assessment
general
```

These intents select bounded detail only. They do not change calculations.

## Routing

The primary router is the existing standard/strong semantic query-understanding model. It now returns three independent source decisions:

```text
needs_vector
needs_graph
needs_sustainability
```

It also returns `document_domains`, `sustainability_intent`, and `needs_leed_assessment`. Deterministic English/Persian policy guards protect explicit LEED and project-carbon requests and provide safe behavior if the router is unavailable; they are not the sole classifier.

| Route | Evidence |
|---|---|
| Conversation | conversation memory only |
| Graph | ordinary IFC/Neo4j facts |
| Regulation | classified document evidence |
| Sustainability | deterministic stored carbon evidence |
| Graph + regulation | existing BIM compliance flow |
| Sustainability + LEED | carbon/material evidence plus classified documents |
| Graph + sustainability + LEED | additional ordinary BIM facts plus both sources |

Conversation memory retains all three source decisions and document domains for contextual follow-ups. Standard and strong modes use different configured router/final models but the same sustainability service, run, and stored values.

## Grounded combination and numeric protection

For a hybrid contributor/guidance question, execution order is:

1. Resolve the exact project/file scope.
2. Retrieve the already-calculated sustainability run and relevant aggregates/details.
3. Retrieve only the requested LEED/sustainability document domains.
4. Construct a conservative evidence-assessment state.
5. Give the final model the separate evidence blocks.
6. Validate documentary citations.
7. Reject project sustainability numeric claims not present in deterministic evidence; allow a documentary numeric requirement only when it is cited and present in the retrieved document context.

The final prompt prohibits invented quantities, factors, `kgCO2e`, requirements, credits, and certification levels. A post-generation numeric guard additionally detects numeric lines about carbon, quantities, factors, mass, area, or volume. Project totals, estimates, contributions, and calculated values must occur in deterministic sustainability evidence. A standard's numeric statement may come from document evidence only when that answer line carries a validated citation. If this check fails, the generated answer is discarded and replaced by bounded extractive evidence. Changing model profile cannot change deterministic values.

A second response guard rejects project-level LEED certification/tier claims and any explicit assessment-state token that differs from the deterministic assessment object.

## Citation behavior

Numeric clauses retain the existing citation form:

```text
[Clause 15-1-1-1, Page 11]
```

LEED and standards often use named rather than numeric sections. A chunk without a numeric clause uses:

```text
[Document leed-v4-1, Section MR-credit-products, Page 12]
```

`Document`, `Section`, and `Page` must exactly match one retrieved source block. Citation validation checks both forms against only the current retrieval. Fabricated document sections/pages and stale citations from conversation history fail validation. Every documentary requirement claim still requires an adjacent validated citation.

## LEED-oriented assessment states

| Status | Meaning |
|---|---|
| `satisfied_from_available_evidence` | A named, reviewed deterministic evaluator returned true from the available inputs. |
| `not_satisfied_from_available_evidence` | A named, reviewed deterministic evaluator returned false from the available inputs. |
| `insufficient_evidence` | Required BIM sustainability evidence, retrieved document evidence, or both are missing. |
| `not_automatically_evaluable` | Both evidence sources exist, but no reviewed deterministic evaluator is registered for that requirement. |

Phase 2 ships the state model and conservative evidence-availability assessment. It does not ship general LEED credit evaluators. Consequently, having both BIM and document evidence normally yields `not_automatically_evaluable`, not a positive or negative credit result.

The system must never report `LEED Certified`, `LEED Silver`, `LEED Gold`, `LEED Platinum`, an awarded credit, or an aggregate certification score.

## Phase 3 dashboard and report handoff

After a hybrid assessment answer passes numeric and citation validation, the orchestrator stores a bounded session snapshot containing the question, conservative state, grounded answer, exact project/file/run scope, document sources, and missing-data categories. The Sustainability view and report endpoint retrieve only snapshots for the same conversation and exact scope.

These findings are not persisted as deterministic Neo4j decisions. They expire with conversation memory, remain clearly labeled session-only, and never feed carbon calculations. JSON, CSV, and HTML reports may include the snapshot and its validated document citations alongside—never merged into—the deterministic result and factor-provenance sections.

## Response contract

`POST /api/ask` retains all prior fields and adds:

```text
used_sustainability
assessment
retrieval_debug.routing.sustainability
retrieval_debug.routing.document_domains
retrieval_debug.sustainability
retrieval_debug.assessment
```

Document sources add `document_id`, `document_domain`, `standard_name`, `standard_version`, and `section_id`. Sustainability sources identify the deterministic run, project/file scope, methodology, and factor dataset hash/version.

## Example outcomes

- “What is the estimated embodied carbon of this project?” retrieves only the current exact-scope sustainability run and copies its stored total.
- “Which materials contribute most and what LEED guidance applies?” retrieves deterministic material aggregates and classified documents, then explains them without recomputing.
- “What LEED material requirements are indexed?” searches only the sustainability document domains and cites retrieved sections.
- “آیا اطلاعات موجود در مدل برای بررسی این معیار LEED کافی است؟” returns `insufficient_evidence` if either side is absent, otherwise `not_automatically_evaluable` unless a reviewed evaluator exists.
- “بیشترین سهم کربن مربوط به کدام مصالح است؟” uses the stored material breakdown and preserves the active project/file scope.

## Tests and verification

Phase 2 regression tests cover:

- backward-compatible document classification and LEED version validation;
- LEED domain filtering across retrieval candidates;
- valid and fabricated named-section citations;
- English and Persian sustainability routing plus semantic-router decisions;
- separate hybrid sustainability/document contexts;
- missing LEED evidence and missing BIM evidence;
- all four assessment states and the deterministic-evaluator gate;
- rejection and safe fallback for an invented carbon number;
- identical deterministic evidence in standard and strong modes;
- API scope and classified upload contracts.

The repository's authoritative environmental limitation remains unchanged: carbon results are only as accurate and applicable as the configured reviewed factor data and IFC evidence.
