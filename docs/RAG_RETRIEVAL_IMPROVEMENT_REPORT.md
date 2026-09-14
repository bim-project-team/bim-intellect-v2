# RAG Retrieval Improvement Report

## Executive result

The investigation reproduced the client failures on a clean, isolated v2 index before any architecture change. The improved v3 index was then built in a separate directory and benchmarked with the same frozen questions and query variants. The original v2 index was not rebuilt. The preserved v3 artifact is:

```text
C:\Users\CMG\AppData\Local\Temp\bim-intellect-rag-improved-v3
collection: regulations_improved_v3
manifest: manifest.json
documents: 7 unique + 1 byte-identical duplicate skipped
chunks: 3,423
index version: 3
```

After the frozen v3 capture, regressions in machine-room hierarchy expansion, broad neighbor expansion, table-seed ordering, Persian paraphrases, and duplicate normalized queries were investigated and fixed. The application collection was rebuilt independently and matches the isolated artifact at 3,423 chunks. The completed live-model evaluation is in `docs/RAG_V3_EVALUATION_REPORT.md`; the latest full suite reports **143 passed, 2 skipped**.

## Experimental controls

Two isolated indexes used the same seven unique PDFs, embedding model, and Chroma distance metric:

| Property | v2 baseline | v3 improved |
|---|---:|---:|
| Directory | `%TEMP%\bim-intellect-rag-baseline-v2` | `%TEMP%\bim-intellect-rag-improved-v3` |
| Collection | `regulations_baseline_v2` | `regulations_improved_v3` |
| Unique PDFs | 7 | 7 |
| Duplicate PDFs skipped | 1 | 1 |
| Physical PDF pages | 763 | 763 |
| Chunks | 3,262 | 3,423 |
| Embedding | multilingual MiniLM L12 v2 | same |

The fixed benchmark used the original client wording plus the same predetermined variants. No model-generated query rewrite was used for the retrieval comparison. The original investigation observed HTTP 403 responses, but access was restored and all three production model IDs returned HTTP 200 on 2026-09-14. The frozen v3 Standard/Strong comparison is now reported separately in `docs/RAG_V3_EVALUATION_REPORT.md` so model-dependent rewriting is not mixed into the architecture benchmark.

## Reproduced pipeline failures and root causes

### Cast-iron flange table, Mabhas 16

The source has 236 physical pages. The relevant table is on physical page 70, printed page 54, under clause `16-3-4-5`. Its cast-iron column contains:

- `EN1092-2`
- `ISO7005-2`
- `ASME16.1`

Plain pypdf extraction flattened the table. More importantly, clause detection treated standard identifiers such as `ASME16.1` and `ASME16.5` as building clauses. The one visual table was consequently fragmented among `16-3-4-5`, false `16-1`, and false `16-5` sections. The baseline context contained only two exact cast-iron codes.

PyMuPDF detected an accurate 4-by-3 cell grid. V3 adds structured row/header chunks while retaining the original text chunks. The table and text chunks both use clause `16-3-4-5`, physical page 70, printed page 54. Final context contains all three rows and the old citation `[Clause 16-3-4-5, Page 70]` remains valid.

### Machine-room completeness, Mabhas 15

Clause `15-2-2-5-2` on physical page 31 contains the individual clearance and working-space dimensions. The baseline retrieved sibling clauses `15-2-2-5-1` and `15-2-2-5-3` but omitted `-2`; the normal client answer therefore told the reader to consult a table and did not state the dimensions.

The frozen v3 run initially reproduced that omission even though `-2` was correctly indexed. The cause was an expansion policy that expanded only the top three exact leaf sections. Multiple high-ranked `15-2-2-5-*` leaves now infer their shared immediate parent and expand its bounded tree. Final context includes the parent plus `-1`, `-2`, and `-3`, the six expected numeric values (`700`, `400`, `500`, `2000`, `300`, and `500 × 600` millimetres), and the shared-machine-room rule.

### Wrong section, Mabhas 24

The PDF title/filename identifies Mabhas 24, but the actual clause namespace printed in the PDF uses root `42`. The old indexer generalized a Mabhas 12 font-encoding workaround and rewrote root `42` to `24`. That corrupted canonical clause metadata.

The exact heading `42-2-3-4 الزامات سطح اشغال و زیربنا` is on physical page 38, printed page 31. The baseline could rank the heading but expanded only the heading chunk; four child clauses containing the real answer never reached final context, so richer wrong sections won generation. There was no six-page copying offset.

V3 limits the known root correction to the verified Mabhas 12 `01 → 12` case, scores headings and explicit Mabhas numbers independently, and expands the parent/child hierarchy. The parent reranks first and all four child clauses reach context.

### Page metadata and page count

V2 stored one ambiguous `page_number`: the 1-based physical PDF page. It neither stored printed page numbers nor total PDF pages. Document status counted only distinct pages represented by chunks. A question such as “مبحث ۲۴ چند صفحه است؟” was consequently answered from the largest retrieved page, 54.

V3 stores physical page, detected printed page, PDF page label, and total PDF pages separately. Mabhas 24 has 55 physical PDF pages. Its target clause is physical page 38 and printed page 31; the earlier benchmark expectation of printed page 30 was corrected after checking the footer. Page-count intent is answered deterministically from metadata and produces a `document_metadata` source rather than a fabricated clause citation.

### Persian numbers and follow-ups

The PDF extraction renders the source typography for the landing-depth value as `38/0 متر`. V2 preserved that string but supplied no deterministic interpretation, and the normal client response repeated it after “۳۸ متر؟؟؟”.

V3 detects slash-decimal aliases only when a nearby measurement unit establishes numeric intent. It preserves the citable source string and adds `38/0 متر = 0.38 متر`; dates without measurement context are not converted. A numeric-disbelief follow-up is deterministically rewritten with the stored topic and an explicit value/unit clarification instruction. The same alias is emitted by the provider-outage extractive fallback.

### Completeness and citations

V2 citation validation checked only membership of `(clause, page)` in current sources. A perfectly cited answer could still omit a table row or list item. V3 builds a coherent checklist from the strongest structured table or enumerated section, validates answer coverage, makes one repair attempt, and falls back to cited extractive evidence if repair remains incomplete.

The validator is intentionally scoped to one coherent enumeration so unrelated lists from neighbor context do not become false requirements. Claim-level entailment is still outside its scope.

## Architecture changes

- Table-aware additive ingestion with PyMuPDF grids and `python-bidi` text handling.
- Standard-code rejection in clause detection, including compact and spaced ASME forms.
- Structured page, document, parent-section, table, and content-kind metadata.
- Always-on lexical candidate generation merged with dense candidates.
- Independent heading and explicit document-number reranker scores.
- Deterministic typo and Persian domain-synonym correction; case-only normalization does not duplicate a dense call.
- Bounded section-tree expansion for headings/tables and shared-parent inference for matching sibling leaves.
- Neighbor traversal starts only from the strongest three ordinary-query hits.
- Deterministic complete-list/table validation, named missing-item repair, and extractive fallback.
- Deterministic document page-count routing.
- Unit-scoped Persian slash-decimal aliases and numeric follow-up clarification.
- Request diagnostics for dense, lexical, reranked, expanded, selected, and numeric-alias stages.

## Before/after retrieval results

The first v3 column below is the frozen measurement taken before any post-capture regression adjustment. Final v3 keeps the same ranks/coverage while reducing redundant expansion and recovering the machine-room parent.

| Query | v2 first correct rerank | frozen v3 | final v3 context coverage |
|---|---:|---:|---|
| Machine-room dimensions | 5 | 1 | 4/4 required sections, 7/7 facts/rules |
| Landing depth | 1 | 2 | correct page; raw and normalized value |
| Vacuum breaker | 1 | 1 | physical page 29; 3/3 definition concepts |
| Cast-iron flange standards | 1 | 1 | 3/3 table rows; text + table chunks |
| Site coverage/floor area | 1 heading only | 1 | parent + four children; 4/4 facts |
| Electrical visual inspection | 1 | 1 | physical pages 67–68; 15/15 list items |
| Elevator call systems | 3 | 1 | both expected sections |

| Aggregate metric | v2 | final v3 |
|---|---:|---:|
| Recall@10, at least one correct section | 7/7 (100%) | 7/7 (100%) |
| MRR, first correct section | ~0.790 | 0.929 |
| Complete required-section coverage | 10/15 (66.7%) | 15/15 (100%) |
| Correct physical-page presence | 9/9 (100%) | 9/9 (100%) |
| Flange table-row coverage | 2/3 (66.7%) | 3/3 (100%) |
| Complete-case fact/item coverage | 18/29 (62.1%) | 29/29 (100%) |
| Extractive citation membership correctness | 7/7 (100%) | 7/7 (100%) |

Correct-page presence did not improve because it was already high: v2 often retrieved the right page while omitting the right chunk, table cell, or child clause. This is why page accuracy must be read with section and row coverage.

Dense retrieval alone missed the Mabhas 24 target in its top ten on v3. The lexical/heading/document stages promoted it to rerank position 1. This is direct evidence that the hybrid architecture—not a new embedding model—caused the improvement.

## Answer-quality comparison

| Case | Recorded/baseline behavior | Improved verified behavior |
|---|---|---|
| Machine room | Exact dimensions omitted; strong answer also omitted shared-room rule. | All supporting sections/facts in context; extractive fallback contains them. Prose generation not tested because provider returned 403. |
| Landing depth | Normal model repeated `38/0` and did not answer numeric disbelief. | Original plus deterministic `0.38 m` alias; topic-bound follow-up rewrite; fallback includes alias. |
| Vacuum breaker | Core definition correct; normal model added an unsupported contamination explanation. | Three source concepts present; extractive fallback has zero added claims. |
| Flanges | Normal answer missed table; strong answer returned 2/3 cast-iron codes. | 3/3 exact rows; deliberately omitted row fails completeness validation. |
| Site coverage | Answer used general density clauses instead of the exact heading’s children. | Four correct child requirements reach context under canonical root `42`. |
| Page count | Highest retrieved page 54 presented as total. | Deterministic total: 55 physical PDF pages. |
| Electrical list | Source supported 15 items. | 15-item coherent checklist; deliberately incomplete answer is rejected. |

Unsupported-claim rate for the bounded extractive output is zero by construction. Live evaluation is now complete: Standard returned 32/40 deterministic required facts and 5/9 fully correct answers; Strong returned 30/40 facts and 6/9 fully correct answers. Citation membership was 100% in both, while semantic evidence alignment was 75.0% and 87.5%, respectively. See `docs/RAG_V3_EVALUATION_REPORT.md` for raw-result links and stage attribution.

## Regressions investigated

- **Machine hierarchy:** correct chunk existed but was outside leaf expansion; fixed with common-parent inference.
- **Vacuum context bloat:** ordinary neighbor walking started from all ten reranked results; limited to the strongest three while retaining page 29.
- **Electrical bloat/false completeness:** broad `22-7` neighbors contributed unrelated lists; checklist selection now uses one coherent enumerated section. Persian paraphrases are normalized to the source vocabulary.
- **Flange broad expansion:** a prose chunk could rank above its table sibling, causing unnecessary `16-3-4` expansion. A table discovered either in reranked results or exact-section expansion now suppresses redundant parent inference. Final expansion is 4 added chunks, not 26.
- **Sustainability domain test:** case-folding `LEED` created a second equivalent dense query. Only substantive correction now creates an additional query; domain filters remain applied to dense and lexical candidates.

## Files changed

- `rag/chunker.py`: table/page extraction, metadata schema, clause protection, numeric aliases.
- `rag/embedder.py`: enriched embedding/storage metadata and hierarchy retrieval.
- `rag/indexer.py`: root correction scope, document/page/table manifest fields.
- `rag/reranker.py`: correction, heading, and document scoring.
- `rag/retrieval.py`: hybrid candidates, diagnostics, bounded/hierarchical expansion, context/source fields.
- `rag/document_metadata.py`: deterministic page-count resolver.
- `rag/orchestrator.py`: follow-up guard, completeness validation/repair, numeric extractive fallback.
- `rag/prompts.py`: table, completeness, numeric, and named-insufficiency instructions.
- `rag/config.py`, `.env.example`: index version 3 and reranker/expansion defaults.
- `static/app.js`: document-metadata citations.
- `requirements.txt`: PyMuPDF and python-bidi.
- `tests/fixtures/rag_regression_benchmark.json`: fixed regression oracle.
- `tests/test_rag_retrieval_improvements.py`: parser, retrieval, completeness, metadata, citation, and follow-up tests.
- `docs/RAG_ARCHITECTURE.md`, `docs/RAG_SYSTEM_WEAKNESSES.md`: operational architecture and final weakness disposition.
- `dataset/sources/rag_index_manifest.json`: reproducible production v3 build manifest.

## Verification

```text
python -m compileall -q rag                         PASS
git diff --check                                    PASS
tests/test_rag_retrieval_improvements.py            22 passed
full suite (PYTEST_DISABLE_PLUGIN_AUTOLOAD=1)        143 passed, 2 skipped, 6 warnings
production collection                               3,423 chunks, index version 3
isolated v3 collection                              3,423 chunks, index version 3
manifest                                             3,423 chunks
```

The skips are unchanged environment-dependent tests. Warnings are third-party deprecations from pytz/PyMuPDF SWIG types and are not RAG failures.

## Remaining limitations and recommended work

1. Add claim-to-evidence entailment validation; live testing confirmed that valid source membership can still cite the wrong evidence.
2. Preserve original Persian technical terms in router variants and reject unsupported translations; Strong mistranslated `خلا شکن` as an expansion joint.
3. Re-anchor completeness follow-ups to prior successful section/table IDs; both profiles drifted from the flange table to appendix pages.
4. Add coordinate/OCR fallback for scanned pages and tests for multi-page tables with repeated headers and merged cells.
5. Replace character TF-IDF corpus scans with a persistent sparse/BM25 index as the corpus grows.
6. Generalize completeness extraction beyond explicit grid rows and lettered lists to prose alternatives while avoiding false checklists.
7. Reconcile the canonical manifest after incremental upload/ingest operations, not only clean builds.
8. Monitor context precision. Recall/completeness improved, but several fixed queries still carry unrelated reranked chunks within the 28,000-character budget.
