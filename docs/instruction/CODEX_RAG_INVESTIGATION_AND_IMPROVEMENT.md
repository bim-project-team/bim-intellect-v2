# Codex Task — Investigate and Improve the RAG / Retrieval System

Review the current BIM-Intellect RAG implementation, its documentation, and the client-reported failures. Treat this as an **investigation and improvement task**, not just a prompt-tuning task.

## 1. First create a weakness document

Create:

`docs/RAG_SYSTEM_WEAKNESSES.md`

This document must clearly describe the current weaknesses of the RAG system, using the existing implementation and the reported client test cases as evidence.

At minimum, document the following problems:

1. **Incomplete answers from tables**
   - Some questions whose answers are stored in tables return only part of the available rows/options.
   - Example: the cast-iron flange standards question returned only part of the expected standards.

2. **Weak table extraction / table understanding**
   - The standard model sometimes retrieves surrounding prose instead of the actual table values.
   - In some cases it tells the user to inspect the table manually instead of extracting the answer.

3. **Incomplete answers even with the strong model**
   - Stronger generation improves wording and interpretation, but does not guarantee answer completeness.
   - Missing retrieved evidence or incomplete table chunks can still produce incomplete answers.

4. **Retrieval from the wrong section of the document**
   - In some tests, the exact answer and even a matching section title existed in the document, but retrieval selected another section several pages away because of overlapping keywords.
   - Investigate whether the main cause is chunking, embeddings, query rewriting, reranking, lexical fallback, section expansion, or metadata handling.

5. **Possible page / source metadata mismatch**
   - The client observed an apparent offset of several pages.
   - Do not assume the system first finds the correct page and then shifts it.
   - Investigate whether the issue comes from PDF physical page numbers, printed document page numbers, chunk metadata, section boundaries, or wrong retrieval.

6. **Incorrect document page-count answers**
   - The current system may infer document length from retrieved page metadata instead of reading actual PDF metadata.
   - Questions such as “How many pages does this document have?” should not be answered from the highest retrieved chunk page.

7. **Persian numeric-format interpretation**
   - Values such as `38/0` may represent `0.38`, but the standard model can repeat them literally without resolving the ambiguity.

8. **Weak follow-up question handling**
   - Follow-ups such as “38 meters???” may repeat the previous answer instead of using conversation context to recognize and correct the misunderstanding.

9. **Retrieval inconsistency between standard and strong modes**
   - The same question may produce very different clauses/pages between model profiles.
   - Determine which parts of retrieval are actually model-dependent and whether query rewriting is causing unstable retrieval.

10. **Citation formatting / citation quality**
    - Multiple citations can appear concatenated or poorly formatted.
    - Also verify that every citation corresponds to evidence actually used in the answer.

11. **Ambiguous insufficient-evidence messages**
    - Messages such as “There is not enough information for this requirement” must identify exactly which requested item or sub-question lacks evidence.

12. **Answer completeness**
    - The current system validates citation grounding, but grounding alone does not guarantee that all requested rows, requirements, alternatives, or list items were returned.
    - Investigate a completeness-validation mechanism.

For each weakness, document:

- observed behavior;
- likely technical cause;
- affected module(s);
- severity;
- whether the issue is retrieval, parsing/chunking, reranking, generation, metadata, conversation handling, or UI formatting;
- how the issue can be reproduced;
- what evidence would confirm the root cause.

---

## 2. Investigate the current implementation before changing it

Read the relevant RAG code completely, especially:

- `rag/chunker.py`
- `rag/retrieval.py`
- `rag/reranker.py`
- `rag/orchestrator.py`
- `rag/prompts.py`
- `rag/embedder.py`
- `rag/document_metadata.py`
- `rag/memory.py`
- `rag/openrouter_client.py`
- API routes related to `/rag/*`, `/ask`, and `/ask-vector`
- existing RAG tests and documentation

Also inspect how PDF page numbers, clause IDs, section IDs, neighbor chunk IDs, table-like text, content hashes, and document metadata are stored in ChromaDB.

Do not assume the client’s proposed cause is correct. Reproduce each failure and identify the actual failure stage.

---

## 3. Run a structured investigation

For each reported query, trace the complete pipeline:

`user question -> standalone query -> query variants -> dense retrieval -> lexical fallback -> reranking -> section/neighbor expansion -> final context -> answer generation -> citation validation`

Log and compare:

- original user query;
- rewritten standalone query;
- generated retrieval variants;
- top dense candidates and scores;
- lexical candidates;
- reranker scores;
- selected chunks;
- page / clause / section metadata;
- final assembled context;
- final answer;
- citations accepted/rejected by validation.

Compare **standard mode vs strong mode** and determine whether differences originate in:

- query understanding;
- query rewriting;
- candidate retrieval;
- reranking;
- context construction;
- final generation.

---

## 4. Focus especially on table-heavy regulatory PDFs

Investigate whether the current PDF extraction and chunking preserve tables adequately.

Check for:

- rows split across chunks;
- columns converted into an unreadable text order;
- table headers separated from values;
- continuation tables split across pages;
- section expansion that omits part of a table;
- duplicate or missing rows;
- chunks that exceed context limits;
- clause detection interfering with tables.

If the existing extraction is insufficient, design and test a more reliable table-aware ingestion strategy while preserving backward compatibility.

Do not rely on the LLM to reconstruct values that are not present in retrieved evidence.

---

## 5. Improve retrieval quality

After identifying root causes, implement the highest-value improvements.

Consider and experimentally evaluate, where appropriate:

- better semantic chunk boundaries;
- table-aware chunks;
- parent/child or section-level retrieval;
- stronger heading/title weighting;
- metadata-aware retrieval;
- clause-aware boosts;
- exact phrase / BM25-style lexical retrieval;
- improved hybrid dense + lexical ranking;
- cross-encoder reranking;
- query decomposition for list/completeness questions;
- multi-stage retrieval;
- retrieving the whole relevant section/table when a completeness request is detected;
- deduplication improvements;
- diversity-aware candidate selection;
- better neighbor expansion;
- reducing false matches caused only by shared keywords.

Do not implement techniques merely because they are popular. Measure whether they improve this corpus.

---

## 6. Add explicit completeness handling

A cited answer can still be incomplete.

Design a mechanism for questions that ask for:

- all items;
- standards;
- requirements;
- alternatives;
- table rows;
- dimensions;
- enumerated options.

The system should detect completeness-sensitive questions and, where possible:

1. retrieve the complete relevant section/table;
2. identify the expected list structure from retrieved evidence;
3. ensure the final answer covers every supported item;
4. fail clearly if the complete set cannot be established.

Do not hallucinate missing rows.

---

## 7. Fix page-number and document-metadata behavior

Separate at least these concepts:

- physical PDF page index;
- printed page number when detectable;
- chunk page metadata;
- total PDF page count.

Document the selected convention.

For direct metadata questions such as document page count, use deterministic document metadata rather than RAG retrieval.

Verify that citations still point to the correct source page after this change.

---

## 8. Improve Persian query and numeric handling

Test Persian regulatory text carefully.

At minimum cover:

- Persian and Arabic digits;
- decimal representations such as `38/0`;
- mixed Persian/English standard names;
- follow-up clarification questions;
- punctuation and spacing variants.

Do not solve this only through the final LLM prompt if deterministic normalization can solve it more reliably.

---

## 9. Build a regression benchmark

Create a small but meaningful RAG regression dataset from the documented client questions.

Include:

- elevator machine-room dimensions;
- minimum stair/landing depth;
- vacuum breaker definition;
- cast-iron flange standards;
- floor-area / site-coverage question;
- document page-count question;
- relevant follow-up questions.

For each case define:

- expected document;
- expected clause/section where known;
- expected page where known;
- required facts/items;
- whether the answer requires complete enumeration;
- allowed wording variation.

Add automated tests where practical.

Evaluate both:

### Retrieval metrics
- Recall@K
- MRR / rank of the correct chunk or section
- correct-section retrieval rate
- correct-page retrieval rate
- table-row coverage

### Answer metrics
- factual correctness
- completeness
- citation correctness
- unsupported-claim rate
- follow-up consistency

Compare **before vs after**.

---

## 10. Preserve current system guarantees

Do not regress:

- bilingual Persian/English support;
- current Chroma document domains;
- clause/page citation validation;
- named-section citations;
- fail-closed behavior;
- conversation memory;
- standard and strong model profiles;
- graph RAG;
- sustainability/LEED routing;
- existing API contracts unless a change is clearly justified and documented.

Run the existing test suite after changes.

---

## 11. Final deliverables

After the investigation and implementation, create:

`docs/RAG_SYSTEM_WEAKNESSES.md`

Updated with confirmed root causes and the implemented fixes.

Also create:

`docs/RAG_RETRIEVAL_IMPROVEMENT_REPORT.md`

The report must contain:

- original problems;
- root-cause analysis;
- architecture changes;
- files changed;
- experiments performed;
- before/after retrieval results;
- before/after answer-quality results;
- remaining limitations;
- recommended future improvements.

Clearly distinguish:

- confirmed bugs;
- retrieval-quality limitations;
- model-quality differences;
- client assumptions that were not confirmed.

The final goal is not merely to make the demonstrated queries pass. Improve the RAG and retrieval architecture so that similar failures across the regulatory corpus are less likely.

Start by investigating and reproducing the current failures. Do not modify the architecture blindly.
