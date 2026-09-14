# RAG v3 Evaluation Report

Evaluation date: 2026-09-14  
Status: completed against the frozen v3 index; no RAG architecture or index changes were made during measurement.

## Executive result

The fixed, model-independent retrieval benchmark is strong: Recall@1/3/5/10 and MRR are all 100%, all 20 required sections and all 10 expected physical pages are present, the Mabhas 16 flange table has 3/3 rows, and final context contains 23/24 deterministic facts. The sole context miss is the derived `38 cm` expression; the source notation `38/0 m` and deterministic `0.38 m` alias are both present.

End-to-end quality is materially lower because model-generated rewriting and answer construction reintroduce failures. Standard achieved 32/40 deterministic required facts and 5/9 fully correct answers; Strong achieved 30/40 facts and 6/9 fully correct answers. Strong was better on the `38/0` follow-up and the electrical 15-item list, but worse on machine-room retrieval and the vacuum-breaker rewrite. It was not uniformly better.

The production citation validator accepted 100% of emitted citations in both profiles, yet manual semantic evidence alignment was only 75.0% for Standard and 87.5% for Strong. This confirms the known limitation: source membership is not claim entailment.

## 1. OpenRouter accessibility

All production models were directly tested outside RAG with `Reply only with: OK`. The requests reached OpenRouter and returned HTTP 200; no evaluation-only substitution was used. No credential is present in any artifact.

| Profile | Model | HTTP status | Accessible | Provider | Error |
|---|---|---:|---|---|---|
| Standard router/final | `openai/gpt-4o-mini` | 200 | yes | Azure | none |
| Strong router | `google/gemini-2.5-flash` | 200 | yes | Google | none |
| Strong final | `anthropic/claude-sonnet-4.5` | 200 | yes | Amazon Bedrock | none |

Raw record: [openrouter_access.json](rag_v3_evaluation_artifacts/openrouter_access.json).

## 2. Frozen system and controls

The isolated v3 index was copied byte-for-byte before evaluation:

| Property | Frozen value |
|---|---|
| Source | `%TEMP%\bim-intellect-rag-improved-v3` |
| Frozen copy | `%TEMP%\bim-intellect-rag-v3-eval-freeze-20260914` |
| Collection | `regulations_improved_v3` |
| Index version | `3` |
| Chunks | 3,423 |
| Files / bytes | 7 / 33,669,227 |
| Git branch | `feature/sustainability` |
| Git HEAD | `0e6df73bf8280cdb188f8939349170d4c9f3386a` |
| Pre-evaluation working-diff SHA-1 | `0174adfb58f074d2aaba464172710d5b55ddeeea` |

The freeze record contains SHA-256 hashes for every Chroma file and the effective retrieval configuration. All hashes matched the source at freeze time. The benchmark runner points only to the frozen copy. Raw record: [freeze_state.json](rag_v3_evaluation_artifacts/freeze_state.json).

The only code added during evaluation was the audit runner [rag_v3_evaluate.py](../scripts/rag_v3_evaluate.py); it does not modify production behavior and was not used to rebuild an index.

## 3. Retrieval-only benchmark

This phase used the fixed questions and predetermined variants, without model rewriting. Every result records dense candidates, lexical candidates, reranker components, expansion reasons, selected chunks, section/page metadata, sources, and final assembled context.

| Metric | Frozen v3 result |
|---|---:|
| Recall@1 | 100% |
| Recall@3 | 100% |
| Recall@5 | 100% |
| Recall@10 | 100% |
| MRR | 1.000 |
| Correct-section case rate | 100% |
| Complete required-section coverage | 20/20 (100%) |
| Correct physical-page coverage | 10/10 (100%) |
| Final-context required-fact coverage | 23/24 (95.8%) |
| Flange table-row coverage | 3/3 (100%) |
| Mean retrieval latency | 4,938.9 ms |

This reproduces the reported v3 section/page/table gains. The earlier report's MRR of 0.929 came from an earlier seven-case snapshot before the final duplicate-query correction; the exact frozen implementation evaluated here places the correct section first in all eight retrieval cases plus the deterministic page-count case. The 23/24 fact result is not an evidence loss: the missing check is the derived phrase `38 cm`, while context retains both the source `38/0 m` and its `0.38 m` interpretation.

Raw trace: [retrieval_raw.json](rag_v3_evaluation_artifacts/retrieval_raw.json).

## 4. Required client-case results

The four stages below are intentionally separate. “Context” means the evidence needed for the expected answer survived final context construction. “Answer” is a manual semantic judgment. “Citations” means semantic support, not merely validator acceptance.

| Case | Fixed retrieval | E2E context/retrieval | Standard answer | Strong answer | Citation finding |
|---|---|---|---|---|---|
| Elevator machine-room dimensions | all required sections/facts | Standard 4/4 sections; Strong missed `15-2-2-5-2` after rewrite | partial, 4/7 facts | partial, 1/7 facts | structurally valid; each missed one target citation section |
| Minimum stair/landing depth | correct `15-3-5-9`, p57 | correct in both | correct `0.38 m` | correct raw + normalized value | correct |
| Persian `38/0` decimal | raw + `0.38 m` alias | correct in both initial turns | normalized value only | raw + normalized value | correct |
| `38 meters???` follow-up | deterministic rewrite feature present | Standard lost target section; Strong retained it | fail-closed, no correction | correct `0.38 m / 38 cm / 380 mm` | Strong correct |
| Vacuum breaker | correct p29 definition, 3/3 concepts | Standard selected p28 parent but not definition; Strong mistranslated and missed Mabhas 16 | wrong falling-tools barrier | wrong “expansion joint / absent” conclusion | valid-looking but semantically wrong in both |
| Cast-iron flange standards | correct clause/page/table | correct for original Persian case in both | 3/3 | 3/3 | correct |
| Complete flange-table enumeration | 3/3 rows; text + table | phrasing-dependent | 3/5 phrasings complete | 2/5 phrasings complete | appendix drift produces valid-looking non-target document citations |
| Mabhas 24 floor area/site coverage | parent + 4 children, p38 | complete in both | correct, 4/4 | correct, 4/4 | correct |
| Mabhas 24 page count | deterministic metadata route | 55 physical pages | correct | correct | document-metadata source, no fabricated clause |
| Exact heading amid similar keywords | exact heading first | parent + all children in both | correct, 4/4 | correct, 4/4 | correct |
| Electrical visual inspection | `22-7-7`, pp67–68, 15/15 | complete in both | facts present only through unwieldy extractive fallback | clean 15-item answer | correct |
| Elevator call system | both expected sections in fixed context | both sections selected in both | wrong/generalized | grounded high-level answer but incomplete | neither cited exact target sections completely |

## 5. Standard-profile end-to-end results

Model path: `openai/gpt-4o-mini` router → `openai/gpt-4o-mini` final.

| Metric | Result |
|---|---:|
| Completed | 9/9 |
| Fully correct answers | 5/9 (55.6%) |
| Material-claim factual support | 7/9 (77.8%) |
| Deterministic required-fact coverage | 32/40 (80.0%) |
| Structurally valid citations | 100% |
| Semantically supporting citations, regulation cases | 6/8 (75.0%) |
| Exact target-section mean citation completeness | 68.75% |
| Unsupported-claim case rate | 2/9 (22.2%) |
| Provider-operation failures | 0 |
| Mean end-to-end case latency | 12,654.9 ms |

Main failures were generation completeness for machine-room dimensions, wrong evidence selection for vacuum breaker, generic unsupported claims for call systems, and a citation/completeness repair path that degraded the electrical result into a large raw extractive dump.

Raw output: [e2e_standard_raw.json](rag_v3_evaluation_artifacts/e2e_standard_raw.json).

## 6. Strong-profile end-to-end results

Model path: `google/gemini-2.5-flash` router → `anthropic/claude-sonnet-4.5` final.

| Metric | Result |
|---|---:|
| Completed | 9/9 |
| Fully correct answers | 6/9 (66.7%) |
| Material-claim factual support | 8/9 (88.9%) |
| Deterministic required-fact coverage | 30/40 (75.0%) |
| Structurally valid citations | 100% |
| Semantically supporting citations, regulation cases | 7/8 (87.5%) |
| Exact target-section mean citation completeness | 68.75% |
| Unsupported-claim case rate | 1/9 (11.1%) |
| Provider-operation failures | 0 |
| Mean end-to-end case latency | 22,434.4 ms |

Strong produced the best stair/follow-up and electrical answers, but its router dropped the machine-room dimension clause and mistranslated `خلا شکن` as “expansion joint.” It was about 1.77× slower by mean case latency.

Raw output: [e2e_strong_raw.json](rag_v3_evaluation_artifacts/e2e_strong_raw.json).

## 7. Standard versus Strong: stage attribution

| Difference | Origin | Evidence |
|---|---|---|
| Strong machine-room regression | query rewriting → retrieval/context | fixed query ranks the target first; Strong rewrite omits `15-2-2-5-2` from selected chunks |
| Strong vacuum-breaker regression | query understanding → retrieval | Gemini explicitly rewrote `خلا شکن` as `(expansion joint)`; correct Mabhas 16 evidence disappeared |
| Standard machine-room omission | final generation/completeness | all four required sections reached context, but only 4/7 required facts were stated |
| Standard electrical degradation | validation/repair → fallback presentation | context/checklist had 15/15 items; final repair failed citation validation and emitted raw extractive evidence |
| Strong electrical improvement | final generation | retrieval was effectively equivalent; Claude returned a clean complete list |
| Mabhas 24 parity | retrieval + context construction | both rewrites preserved the exact heading and four-child expansion; both answers were complete |
| Flange original-query parity | retrieval + generation | both selected clause `16-3-4-5` and returned 3/3 rows |
| Flange follow-up failure | rewrite preserved topic, retrieval drifted | both profiles searched the standards appendix rather than the clause-70 structured table |

The strong profile is not a retrieval invariant: the shared frozen index and reranker receive different model-generated standalone queries and variants.

## 8. Completeness and table behavior

Five paraphrases requested the complete cast-iron flange set.

| Metric | Standard | Strong |
|---|---:|---:|
| All three target rows | 3/5 (60%) | 2/5 (40%) |
| Missing rows in successful cases | 0 | 0 |
| Invented target rows | 0 | 0 |
| Header/category context retained | 3/5 (60%) | 2/5 (40%) |
| Free of extraneous standard-like tokens | 5/5 (100%) | 1/5 (20%) |

The failure is not table ingestion. Frozen retrieval shows both:

- original text chunk `...-p70-c310`, `chunk_kind=text`;
- structured chunk `...-p70-table0-part0`, `chunk_kind=table`.

Both use clause `16-3-4-5`, physical page 70, printed page 54, so the backward-compatible citation `[Clause 16-3-4-5, Page 70]` remains valid. Failures came from routing/retrieval drift: one Standard phrasing was routed only to the empty `standard` domain; English/Strong phrasings frequently expanded appendix pages 226–235. Strong's additional standards were usually found in that unrelated context rather than fabricated, but they were not rows of the requested table.

Raw outputs: [completeness_standard_raw.json](rag_v3_evaluation_artifacts/completeness_standard_raw.json), [completeness_strong_raw.json](rag_v3_evaluation_artifacts/completeness_strong_raw.json).

## 9. Conversational follow-ups

| Metric | Standard | Strong |
|---|---:|---:|
| Correct subject-section retention on follow-up turns | 1/4 (25%) | 2/4 (50%) |
| Final `38 meters???` correction | fail | pass |
| Final flange re-enumeration | fail, 1/3 target rows | fail, 1/3 target rows |
| Structural citation correctness | 75% | 100% |

Standard correctly resolved `عمق پله؟` to the original question, but its deterministic numeric-disbelief rewrite retrieved neighboring stair material and the answer failed closed. Strong retained `15-3-5-9` and explicitly corrected `38 m` to `0.38 m`, `38 cm`, and `380 mm`.

In the flange conversation, both routers preserved the phrase “cast-iron flanges,” but all turns drifted to the standards appendix. Standard enumerated many unrelated ISO standards; Strong narrowed to the appendix's ISO/ASME entries but repeatedly omitted `EN1092-2`. Topic retention at the language layer therefore did not guarantee target-section retention.

Raw outputs: [followups_standard_raw.json](rag_v3_evaluation_artifacts/followups_standard_raw.json), [followups_strong_raw.json](rag_v3_evaluation_artifacts/followups_strong_raw.json).

## 10. Negative and unanswerable questions

The three absent facts were liquid-hydrogen pressure, a space-elevator standard, and Mars helipad site coverage.

| Manually audited metric | Standard | Strong |
|---|---:|---:|
| Clear insufficient-evidence behavior | 3/3 (100%) | 3/3 (100%) |
| Hallucinated requested answer | 0/3 | 0/3 |
| Invented citation | 0/3 | 0/3 |
| Unsupported numeric answer | 0/3 | 0/3 |
| Extraneous supported numeric examples | 0/3 | 2/3 |
| Unsupported non-numeric claim | 0/3 | 1/3 |

The raw automatic metric reported 1/3 for Standard and 0/3 for Strong. Manual review overrides it because the detector incorrectly required a citation on a refusal and did not recognize longer Persian “no information is provided” constructions. Standard used its fail-closed citation-suppression response twice. Strong clearly refused all three but added distracting retrieved facts; its hydrogen answer also incorrectly suggested Mabhas 15 as a likely mechanical-systems source.

Raw outputs: [negative_standard_raw.json](rag_v3_evaluation_artifacts/negative_standard_raw.json), [negative_strong_raw.json](rag_v3_evaluation_artifacts/negative_strong_raw.json). Corrected audit: [manual_semantic_audit.json](rag_v3_evaluation_artifacts/manual_semantic_audit.json).

## 11. Metadata, hierarchy, and numeric verification

- **Mabhas 24 clause root:** the source's internal `42-…` namespace is retained and is not rewritten to `24-…` merely because the filename says Mabhas 24.
- **Mabhas 24 page map:** `42-2-3-4` is physical PDF page 38 and printed page 31. Its four children share that mapping.
- **Page count:** 55 physical PDF pages comes from persisted PDF metadata, not the highest retrieved chunk.
- **Hierarchy:** `42-2-3-4` reranks first; all four child clauses are added with `complete_section_tree`. Machine-room fixed retrieval similarly contains parent and siblings, including `15-2-2-5-2`.
- **Persian slash decimal:** context preserves `38/0 m` and adds `0.38 m`; initial Strong generation interprets it correctly. Only the Strong conversational run successfully delivered the final disbelief correction.
- **Backward compatibility:** additive table chunks coexist with original text chunks under the same canonical clause/page citation.

## 12. Regression and compatibility tests

The first pytest attempt was blocked before collection by a globally installed Hydra/OmegaConf plugin whose ANTLR runtime was incompatible. With third-party plugin autoload disabled, project tests ran normally.

| Suite | Result | Reference |
|---|---:|---:|
| Focused RAG/parser/retrieval/citation/memory/API selection | 70 passed | no failures |
| Full project suite | 143 passed, 2 skipped | 142 passed, 3 skipped |

There are no regressions relative to the reference. JUnit artifacts: [focused_rag_junit.xml](rag_v3_evaluation_artifacts/focused_rag_junit.xml), [full_regression_junit.xml](rag_v3_evaluation_artifacts/full_regression_junit.xml), and [test_results.json](rag_v3_evaluation_artifacts/test_results.json).

## 13. Remaining failures and recommended next work

No changes below were made during the frozen benchmark.

1. Add semantic/claim-level citation entailment. Current validation catches fabricated clause/page pairs but accepts valid citations to irrelevant evidence.
2. Constrain router translations for Persian technical terms. Preserve the original term in every retrieval query and reject unsupported English glosses such as `خلا شکن → expansion joint`.
3. Make domain routing inclusive for regulatory questions containing “standard”; the empty `standard` domain must not suppress regulation retrieval.
4. Prefer clause/table evidence over the document standards appendix when a request asks for standards attached to a named component or source table.
5. Carry target section/chunk identity in conversation memory, not only the linguistic topic, and re-anchor complete follow-ups to the prior successful evidence.
6. Validate completeness against answer-visible rows after repair. If repair fails, return a concise deterministic table/list rather than a broad raw context dump.
7. Refine evaluation detectors so refusals need no citation, Persian insufficient-evidence wording is recognized, numeric flags ignore list/citation/document ordinals, and citation completeness requires exact target sections rather than accepting broad parents.
8. Add a stable semantic regression for the vacuum-breaker definition and the two three-turn conversations, since unit-level rewrite tests alone did not predict live router behavior.

## Artifact inventory

All raw benchmark outputs and evaluation logs are under [rag_v3_evaluation_artifacts](rag_v3_evaluation_artifacts/):

- `retrieval_raw.json`
- `e2e_standard_raw.json`, `e2e_strong_raw.json`
- `negative_standard_raw.json`, `negative_strong_raw.json`
- `completeness_standard_raw.json`, `completeness_strong_raw.json`
- `followups_standard_raw.json`, `followups_strong_raw.json`
- `manual_semantic_audit.json`
- `openrouter_access.json`, `freeze_state.json`
- `evaluation.log`
- focused/full JUnit XML and `test_results.json`

The raw JSON includes effective non-secret configuration, model IDs, rewrites, retrieval queries, selected chunk IDs and diagnostics, answers, recognized citations, latency, validation outcomes, and provider-operation events.
