# RAG System Weaknesses

## Status and evidence basis

This register began as the required investigation-first assessment and was finalized after controlled v2/v3 builds, fixed-query traces, targeted ablations, and regression testing. The final dispositions below are authoritative; the longer entries preserve the initial reproduction hypotheses for auditability.

At investigation start, the live `regulations_v2` collection was not reproducible: it contained 1,961 chunks, omitted Mabhas 16 and 24, duplicated byte-identical Mabhas 15 content, and disagreed with its 2,251-chunk manifest. A clean isolated v2 baseline contained 3,262 chunks. The preserved isolated v3 collection and rebuilt production collection now each contain 3,423 chunks from seven unique PDFs; the duplicate Mabhas 15 PDF is skipped and the generated manifest matches the production count.

## Final confirmed findings and disposition

| ID | Confirmed root cause | Final disposition |
|---|---|---|
| RAG-01/02 | Plain pypdf text flattened table cells; `ASME16.1` and `ASME16.5` were also misread as clauses, splitting the physical-page-70 flange table across false sections. | Fixed for detected tables: original text chunks remain and PyMuPDF grid chunks add header/value relationships. Standard identifiers are excluded from clause detection. Multi-page/scanned tables remain a limitation. |
| RAG-03/12 | “Complete” affected expansion and prompting only. No answer-level row/list coverage validator existed, and leaf-only expansion could omit a sibling clause. | Hierarchical/common-parent expansion, coherent table/list checklists, one repair attempt, and a grounded extractive fallback were added. Live v3 still completes only 3/5 Standard and 2/5 Strong flange-table phrasings because routing can miss the target table; free-form prose enumerations remain limited. |
| RAG-04 | Dense/character similarity rewarded shared words; headings and requested Mabhas numbers had no independent scores. The correct Mabhas 24 heading could be found but its child clauses were not assembled. | Lexical candidates now always join dense candidates; heading/document scores and deterministic Persian synonym correction were added. The Mabhas 24 parent reranks first and all four children reach context. |
| RAG-05 | `page_number` meant physical PDF page only; printed pages and PDF totals were absent. The reported “six-page shift” was wrong-section retrieval, not a copying offset. | Physical, printed, label, and total-page metadata are separate. Mabhas 24 target is physical 38 / printed 31. The client shift assumption was not confirmed. |
| RAG-06 | Page count was inferred from the largest retrieved chunk page because total PDF pages were not persisted or routed deterministically. | Fixed: Mabhas 24 returns 55 physical PDF pages from metadata with a `document_metadata` source. |
| RAG-07/08 | Digits were normalized but RTL slash-decimals were not; offline follow-up fallback merely prepended the topic and repeated `38/0`. | Conservative unit-scoped aliases preserve `38/0 متر` and expose `0.38 متر`. Numeric-disbelief follow-ups are rewritten as topic-bound value/unit clarification. The extractive fallback also emits the alias. |
| RAG-09 | Standard/strong modes share retrieval but use different router/final models, so model rewrites can change candidates. | Fixed-query retrieval is mode-independent and perfect on the frozen benchmark, but live evaluation confirms rewrite divergence: Strong dropped the machine-room dimension clause and mistranslated `خلا شکن`. This remains open. |
| RAG-10 | Citation validation proves only that a clause/page pair is in current sources; it does not prove entailment. Historical chip concatenation was not reproducible in the current flex-gap UI. | Table/text chunks retain the old clause/page contract and request-scoped validation passes. Claim-level entailment validation remains a limitation. Document-metadata source chips are supported. |
| RAG-11 | The prompt mandated an unnamed “insufficient information” sentence. | Fixed: missing evidence must name the unsupported requested item. |
| RAG-13 | Incremental collection state and the manifest had drifted; build-time deduplication did not describe prior uploads. | Current production state is reconciled and reproducible. Automatic manifest reconciliation after every incremental upload is still recommended. |

## Experimental closure

- Fixed-query retrieval Recall@10 stayed 7/7 while MRR improved from approximately 0.790 to 0.929.
- Complete-section coverage improved from 10/15 expected sections to 15/15.
- Flange table-row coverage improved from 2/3 to 3/3; page 70 contains both `text` and `table` chunks under clause `16-3-4-5`.
- Correct physical-page presence was 9/9 before and after, demonstrating why page accuracy alone did not reveal missing rows/children.
- Extractive citation validation was 7/7 before and after. The historical client citation `Page 226` was invalid, but current validation correctly restricts citations to retrieved pairs.
- Live Standard/Strong evaluation completed on 2026-09-14; detailed results and raw-output links are in `docs/RAG_V3_EVALUATION_REPORT.md`.
- Final regression result: 143 passed, 2 skipped, 6 dependency warnings.

## Summary register

| ID | Weakness | Severity | Primary stage | Initial status |
|---|---|---:|---|---|
| RAG-01 | Incomplete answers from tables | Critical | parsing/chunking, retrieval, completeness | fixed for detected grid tables |
| RAG-02 | Weak table extraction and understanding | Critical | PDF parsing/chunking | mitigated; OCR/multi-page tables remain |
| RAG-03 | Incomplete answers in strong mode | High | retrieval/context, generation | partially mitigated; live Strong was complete on 2/5 flange phrasings and 1/7 machine-room facts |
| RAG-04 | Wrong-section retrieval | Critical | chunking, hybrid ranking, metadata | fixed on benchmark; monitored by regression tests |
| RAG-05 | Page/source metadata ambiguity | High | metadata, citation UI | fixed; claimed offset not confirmed |
| RAG-06 | Incorrect document page-count answers | High | metadata routing/generation | fixed |
| RAG-07 | Persian numeric-format interpretation | High | normalization, generation | fixed conservatively |
| RAG-08 | Weak follow-up handling | High | conversation understanding | numeric rewrite exists, but live Standard failed `38 meters???` and both profiles failed the flange re-enumeration |
| RAG-09 | Standard/strong retrieval inconsistency | High | model-dependent query rewriting | fixed-query layer is stable; live model rewrites still diverge and can regress retrieval |
| RAG-10 | Citation formatting/quality | Medium | validation, API/UI formatting | contract preserved; entailment remains |
| RAG-11 | Ambiguous insufficient-evidence messages | Medium | generation prompt | fixed |
| RAG-12 | Grounded but incomplete answers | Critical | completeness validation | fixed for structured tables/lists; prose remains |
| RAG-13 | Corpus/index/manifest drift | Critical | ingestion/operations | current state fixed; incremental reconciliation remains |

## Initial investigation detail (historical hypotheses)

### RAG-01 — Incomplete answers from tables

- **Observed behavior:** For “استاندرادهایی که برای انتخاب فلنچ چدنی باید بدونیم چیا هستند”, the strong answer returned only part of the expected standards; the standard answer did not return the table rows at all. The client specifically reports that one of three expected cast-iron flange choices was omitted.
- **Likely technical cause:** **Hypothesis:** pypdf reading order flattens cells without preserving row/header relationships; clause segmentation can split a table at numeric strings mistaken for clauses; a table may span page or section IDs; normal neighbor expansion retrieves only linked chunks in one detected section. Even if all evidence reaches the final prompt, there is no deterministic row-coverage check.
- **Affected modules:** `rag/chunker.py`, `rag/embedder.py`, `rag/retrieval.py`, `rag/orchestrator.py`, `rag/prompts.py`.
- **Classification:** parsing/chunking + retrieval + generation/completeness.
- **Severity:** Critical for regulatory use.
- **Reproduction:** Index `16 - v4-1396 - تاسیسات بهداشتی.pdf`; run the exact client query in both modes; compare raw page/table text, produced chunks, dense/lexical candidates, expanded context, and enumerated standards.
- **Confirmation evidence:** The expected table header and every row must be identifiable in raw extraction and chunks. Table-row coverage at each stage will show whether loss occurs during parsing, chunking, retrieval, context budgeting, or generation.

### RAG-02 — Weak table extraction and table understanding

- **Observed behavior:** The standard model retrieves nearby prose or tells the user to consult a table rather than returning its values. Elevator machine-room dimensions and flange standards both demonstrate this pattern.
- **Likely technical cause:** **Confirmed design limitation:** `extract_text_by_page()` uses plain `pypdf.Page.extract_text()` and the `Chunk` schema has no table identity, header, row, continuation, or parent-table metadata. `_logical_blocks()` only understands blank lines and list markers. `_extract_heading()` uses the first extracted line, which can be a cell or page artifact rather than a section heading.
- **Affected modules:** `rag/chunker.py`, `rag/embedder.py`, `rag/indexer.py`.
- **Classification:** parsing/chunking.
- **Severity:** Critical.
- **Reproduction:** Dump pages containing the Mabhas 15 machine-room table and Mabhas 16 flange table; inspect order, repeated headers, chunk boundaries, section IDs, and exact row preservation.
- **Confirmation evidence:** Compare PyMuPDF/pypdf text and word-coordinate/table extraction; measure expected row preservation and retrieval coverage.

### RAG-03 — Incomplete answers even in strong mode

- **Observed behavior:** Strong mode omitted “موتورخانه مشترک” and at least one flange-table option while producing better prose.
- **Likely technical cause:** **Confirmed architectural fact:** both modes use the same embedder, Chroma index, hybrid reranker, expansion, and context assembler, but different router/final models. Strong mode can alter standalone queries and variants, but cannot recover evidence absent from candidates/context. The prompt asks the model to preserve items but no validator verifies that it did.
- **Affected modules:** `rag/orchestrator.py`, `rag/retrieval.py`, `rag/prompts.py`.
- **Classification:** query understanding + retrieval + generation/completeness.
- **Severity:** High.
- **Reproduction:** Freeze identical queries/variants to isolate final generation, then compare normal mode-specific rewrites to isolate query-understanding effects.
- **Confirmation evidence:** Context row/item coverage versus answer row/item coverage for both profiles.

### RAG-04 — Retrieval from the wrong section

- **Observed behavior:** The surface-area/site-coverage query returned general density criteria from a section roughly six PDF pages away even though a matching heading and explicit options existed elsewhere.
- **Likely technical cause:** **Hypothesis:** dense similarity and character n-gram TF-IDF reward shared vocabulary without giving exact headings or clause/title matches a dedicated score. The embedding prefix includes the heading, but the lexical reranker scores only `Candidate.text`. No document/chapter hint extraction, exact phrase score, BM25 index, or heading boost exists. Expansion then reinforces whichever wrong section wins initially.
- **Affected modules:** `rag/embedder.py`, `rag/reranker.py`, `rag/retrieval.py`, `rag/chunker.py`.
- **Classification:** retrieval/reranking + metadata.
- **Severity:** Critical.
- **Reproduction:** Trace the exact Mabhas 24 query through candidate ranks and inspect the correct heading’s rank at dense and rerank stages.
- **Confirmation evidence:** Correct-section rank, competing-section score components, and ablations for heading/exact-phrase/document boosts.

### RAG-05 — Possible page/source metadata mismatch

- **Observed behavior:** The client inferred an approximately six-page shift between the correct content and cited content.
- **Likely technical cause:** **Confirmed ambiguity, unconfirmed shift:** `page_number` is only `enumerate(reader.pages, start=1)`, i.e. physical PDF page number. Printed page labels are not extracted or stored. A wrong retrieved section therefore looks like a shifted page. `list_indexed_documents()` also reports the count of distinct chunk pages, not total physical pages, so cover/blank/skipped appendix pages disappear.
- **Affected modules:** `rag/chunker.py`, `rag/embedder.py`, `rag/document_metadata.py`, `rag/retrieval.py`, API citation rendering.
- **Classification:** metadata + retrieval.
- **Severity:** High.
- **Reproduction:** Compare PDF physical index, printed footer/header number, chunk page, and cited page for every client case.
- **Confirmation evidence:** A four-column page map will determine whether an offset exists or retrieval simply selected another physical page.

### RAG-06 — Incorrect document page-count answers

- **Observed behavior:** “مبحث ۲۴ چند صفحه است؟” was answered from the highest retrieved chunk page (54) rather than deterministic PDF metadata.
- **Likely technical cause:** **Confirmed design gap:** there is no metadata-question route and no persisted `total_pdf_pages`. The final model only sees retrieved chunk pages. `list_indexed_documents().page_count` is the number of distinct pages represented by chunks, not `len(PdfReader.pages)`.
- **Affected modules:** `rag/document_metadata.py`, `rag/embedder.py`, `rag/indexer.py`, `rag/orchestrator.py`, API status route.
- **Classification:** metadata + routing/generation.
- **Severity:** High.
- **Reproduction:** Ask page-count questions for each indexed document and compare against `len(PdfReader(path).pages)`.
- **Confirmation evidence:** deterministic metadata answer and tests for physical total versus represented chunk pages.

### RAG-07 — Persian numeric-format interpretation

- **Observed behavior:** The standard answer repeated `38/0 متر`; the user interpreted it as 38 metres. Strong mode converted it to 0.38 m / 38 cm.
- **Likely technical cause:** **Confirmed design gap:** Unicode digits are normalized, but Persian slash-decimal notation is not. The generation prompt actually forbids conversions because every numeric value must appear verbatim in context. Therefore model-dependent interpretation is accidental and inconsistent.
- **Affected modules:** `rag/chunker.py`, `rag/retrieval.py`, `rag/prompts.py`, `rag/orchestrator.py`.
- **Classification:** deterministic normalization + generation.
- **Severity:** High because units/dimensions are safety relevant.
- **Reproduction:** Test Persian/Arabic/ASCII digits with `38/0`, `۳۸/۰`, `٣٨/٠`, punctuation, and the “۳۸ متر؟؟؟” follow-up.
- **Confirmation evidence:** conservative numeric aliasing supported by source typography and unit context, with original text preserved for citations.

### RAG-08 — Weak follow-up question handling

- **Observed behavior:** After “عمق پله؟”, “۳۸ متر؟؟؟” repeated the ambiguous source string rather than recognizing a correction request.
- **Likely technical cause:** **Partly confirmed:** follow-up interpretation is delegated to the router LLM. The offline fallback prepends the topic but does not recognize numeric disbelief/correction intent, normalize the disputed value, or explicitly request clarification evidence. Memory stores only topic/messages and unbounded accumulated chunk IDs; it stores no structured claims/units from the previous answer.
- **Affected modules:** `rag/orchestrator.py`, `rag/memory.py`, `rag/prompts.py`.
- **Classification:** conversation understanding + numeric handling.
- **Severity:** High.
- **Reproduction:** Run the three-turn stair sequence with the same conversation ID in standard/strong and with router failure mocked.
- **Confirmation evidence:** rewritten standalone query, completeness flag, selected evidence, and corrected answer.

### RAG-09 — Retrieval inconsistency between standard and strong modes

- **Observed behavior:** The same flange question produced very different clauses/pages; one recorded citation was impossible (`Page 226`) for the actual PDF.
- **Likely technical cause:** **Confirmed architecture:** `profile.router_model` controls standalone rewriting and retrieval variants, so retrieval is indirectly model-dependent even though embeddings/reranking are shared. The modes also use different final models. **Hypothesis:** unstable variants amplify different keyword neighborhoods. The page-226 citation additionally suggests a fabricated citation or a differently indexed source and should have failed current request-scoped citation validation.
- **Affected modules:** `rag/orchestrator.py`, `rag/prompts.py`, `rag/retrieval.py`.
- **Classification:** query rewriting + retrieval + generation/validation.
- **Severity:** High.
- **Reproduction:** log both modes’ understanding JSON and run an ablation with fixed query variants.
- **Confirmation evidence:** stage where ranks diverge and whether page 226 exists in current sources.

### RAG-10 — Citation formatting and citation quality

- **Observed behavior:** UI source chips/exports concatenate citations without separators. Client outputs also include citations whose evidence relevance is questionable.
- **Likely technical cause:** **Confirmed limitation:** validation proves only that a `(clause_id, page_number)` pair occurs in current sources. It does not prove adjacency, entailment, claim support, or complete usage. `sources_used_by_answer()` works at clause/page granularity, so citing one claim can retain multiple chunks with the same pair. The historical concatenation may be UI rendering rather than answer Markdown.
- **Affected modules:** `rag/orchestrator.py`, `static/app.js`, `static/style.css`, `rag/prompts.py`.
- **Classification:** citation validation + UI formatting.
- **Severity:** Medium, elevated when a valid-looking citation supports the wrong claim.
- **Reproduction:** inspect raw API answer versus rendered chip container for multi-citation answers; inject valid-but-irrelevant citations in unit tests.
- **Confirmation evidence:** current-request citation match, claim/evidence mapping, and rendered separator/line-break tests.

### RAG-11 — Ambiguous insufficient-evidence messages

- **Observed behavior:** The machine-room answer ends with “اطلاعات کافی برای این الزام ...” without identifying which missing requirement is meant.
- **Likely technical cause:** **Confirmed prompt bug:** `COMBINE_PROMPT` mandates that exact generic sentence and supplies no field for the missing sub-question/item.
- **Affected modules:** `rag/prompts.py`, answer validation.
- **Classification:** generation/UI clarity.
- **Severity:** Medium.
- **Reproduction:** ask a multi-part question where only one requested item is absent.
- **Confirmation evidence:** output should name the unsupported item and cite supported items separately.

### RAG-12 — Citation grounding does not ensure completeness

- **Observed behavior:** Answers pass citation checks while omitting list items, table rows, alternatives, or entire requested subtopics.
- **Likely technical cause:** **Confirmed missing mechanism:** `validate_generated_answer()` checks citation membership only. `completeness_requested` changes section expansion and prompt wording, but there is no deterministic detection fallback, expected-item structure, context coverage check, answer coverage check, or fail-closed incomplete-set status.
- **Affected modules:** `rag/orchestrator.py`, `rag/retrieval.py`, `rag/prompts.py`.
- **Classification:** completeness validation.
- **Severity:** Critical.
- **Reproduction:** use electrical 15-item clause, flange table, and machine-room multi-requirement question; intentionally omit one supported item in a mocked answer.
- **Confirmation evidence:** expected item IDs/rows from selected evidence compared with answer coverage; incomplete answers must be repaired or explicitly report the named missing set.

### RAG-13 — Corpus/index/manifest drift

- **Observed behavior:** On 2026-09-14 the live collection count is 1,961 while the manifest claims 2,251. Mabhas 16/24 source PDFs are not indexed; duplicate Mabhas 15 content is indexed under two document IDs; two upload-only files are present but absent from the manifest.
- **Likely technical cause:** **Confirmed operational bug:** incremental upload mutates the collection without updating a canonical manifest; byte-identical deduplication happens only inside one `build_index()` invocation; status derives documents from chunks and cannot report source/manifest drift.
- **Affected modules:** `rag/indexer.py`, `rag/embedder.py`, API upload/status routes.
- **Classification:** ingestion/metadata/operations.
- **Severity:** Critical because it makes client reproduction and retrieval results non-deterministic.
- **Reproduction:** compare `python -m rag.indexer --status`, `list_indexed_documents()`, manifest hashes, and current `dataset/sources` files.
- **Confirmation evidence:** reproducible clean build with matching manifest/collection counts and duplicate hashes represented once.

## Investigation acceptance criteria

For each benchmark query the trace must record: original question, standalone query, variants, dense candidates/scores, lexical candidates/scores, reranker components/scores, expansion reason, selected chunks and structural/page metadata, final context, generated answer, completeness result, and citations accepted/rejected. Before/after experiments must use the same corpus snapshot and fixed retrieval queries when measuring retrieval architecture, then separately compare standard/strong model-dependent rewriting and generation.
