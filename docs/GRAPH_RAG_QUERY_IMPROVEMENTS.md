# Graph/RAG Query Planning Improvements

## Scope and regression evidence

The repository contains the supplied black-box cases in `.test-rag/` (there is no `.rag-test/` directory in the current checkout). The recorded strong-mode answers exposed four incomplete graph behaviors while the typed `CLASHES_WITH` listing demonstrated that relationship-level retrieval itself was healthy.

The failures were:

1. a request for counts grouped by `CLASHES_WITH.issue` returned only one total;
2. a storey-scoped request lost both the grouping and the “either endpoint” condition;
3. an anomaly-property request queried `anomalyScore` but omitted `isAnomaly`;
4. an exact IFC-type request was misrouted to document retrieval and returned a PDF-evidence failure.

## Root causes and previous flow

The previous graph path generated one free-form Cypher statement and executed it immediately. There was no structured representation of requested outputs and no check that returned columns matched the question. Consequently, the final model could recognize missing data but could not retrieve it. Routing was primarily semantic, so an explicit schema question could still be assigned to vector retrieval. Empty graph/error behavior then shared the generic document-evidence message.

## Actual Neo4j schema

Chat queries use `(:Element)` nodes with `id`, `ifcGuid`, `ifcType`, `name`, `storeyId`, `storeyName`, project/source properties, and bounding-box properties. IFC classes are also dynamic labels, but exact-type plans compare the authoritative `ifcType` property. Stored directed `[:CLASHES_WITH]` relationships have:

- `r.issue`: `CLASH` or `CLEARANCE_VIOLATION`;
- `r.metric`: penetration volume or clearance gap;
- optional anomaly enrichment fields when anomaly scoring has run.

Element anomaly fields are optional `anomalyScore` and `isAnomaly` properties. Their absence is valid data and yields zero presence counts.

## New planning and completeness flow

`bim_graph.query_planner` recognizes high-risk result shapes before free-form generation. A plan contains:

- graph intent;
- parameterized read-only Cypher;
- query parameters;
- requested semantic outputs;
- required returned columns.

The execution flow is now:

```text
question
  -> deterministic template (narrow legacy cases)
  -> schema-aware query plan (aggregates/exact types/anomaly/listing)
  -> free-form Gemini Cypher only for novel shapes
  -> EXPLAIN validation
  -> parameterized execution
  -> returned-column completeness validation
  -> structured graph context
  -> grounded final answer
```

Recognized plans produce all requested outputs in one complete query, which is preferable to knowingly running a partial query. If execution nevertheless returns a shape missing required aliases, one corrective Cypher call receives the original question, previous query, and missing-column contract. The repaired query is independently safety-checked and executed once. If it remains incomplete, the graph path fails closed rather than presenting partial data. Novel shapes retain the existing free-form path. The maximum is two queries; the system never loops indefinitely or asks the user to issue a query it can construct itself.

## Aggregation and grouping

Separate issue counts use the real `r.issue` property and alias it for answer clarity:

```cypher
MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
RETURN r.issue AS issue_type, count(r) AS issue_count
ORDER BY issue_type
```

This preserves `CLASH != CLEARANCE_VIOLATION` and never substitutes a total relationship count for grouped counts.

## Storey filtering and directionality

Storey plans parameterize the exact raw IFC storey name and include a relationship when either endpoint matches:

```cypher
MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
WHERE a.storeyName = $storey OR b.storeyName = $storey
RETURN r.issue AS issue_type, count(r) AS issue_count
ORDER BY issue_type
```

The query matches the stored directed relationship exactly once. It does not use an undirected match that can obscure directionality or introduce double-counting risks.

## Anomaly properties

Both requested optional properties are queried together and only on `Element` nodes:

```cypher
MATCH (e:Element)
RETURN
  count(CASE WHEN e.anomalyScore IS NOT NULL THEN 1 END) AS scored_count,
  count(CASE WHEN e.isAnomaly = true THEN 1 END) AS anomaly_count
```

This does not read training artifacts and does not reinterpret deterministic clashes as ML anomalies.

## Exact IFC types

All explicit `Ifc...` tokens are retained as distinct parameters. For two requested types the plan has the following shape:

```cypher
MATCH (e:Element)
RETURN
  sum(CASE WHEN e.ifcType = $ifc_type_0 THEN 1 ELSE 0 END) AS ifc_type_0_count,
  sum(CASE WHEN e.ifcType = $ifc_type_1 THEN 1 ELSE 0 END) AS ifc_type_1_count
```

No fuzzy match, label substitution, superclass expansion, or `IfcPipe`/`IfcFlowSegment` conflation occurs.

## Detailed relationship listings

Questions asking for two explicit IFC types plus relationship details use parameterized endpoint type comparisons in either orientation. The projection includes issue, metric, both names, and both IDs, and remains bounded by `LIMIT 50`. Formatting still caps final-model context to 25 rows for token safety and adds endpoint source metadata for the UI.

## Routing and failure separation

Explicit IFC tokens, Neo4j, `CLASHES_WITH`, issue constants, and anomaly property names force graph retrieval. Unless the question also expresses a regulatory/document intent, these signals disable vector retrieval. The rule is applied after semantic routing, so it guards both standard and strong model profiles.

Graph-only empty results now produce a graph-specific “no matching Neo4j record” response. Graph execution failures produce a distinct error response and never masquerade as missing PDF evidence.

## Cypher safety

Planner queries are parameterized and read-only. Free-form generation:

- permits only `MATCH`, `RETURN`, `SHOW`, or `WITH` as the first clause;
- rejects mutation clauses and dangerous procedures using token-boundary checks;
- rejects multiple statements;
- retains the existing aggregate exception to list-query limits;
- validates generated queries with `EXPLAIN` before execution.

## Strong-model behavior and logging

Strong mode continues using Gemini for contextual routing/free-form Cypher and Claude for grounded wording. Recognized high-risk shapes do not depend on Gemini reproducing subtle aggregation semantics. Logs now include intent, requested outputs, required columns, safe parameters, Cypher source/query, returned row count/columns, and completeness status/missing columns. No credentials or document bodies are logged.

## Tests

`tests/test_graph_query_planner.py` covers:

- grouped issue counts rather than one total;
- exact storey preservation, either-endpoint filtering, and directed counting;
- both anomaly properties;
- multiple distinct, parameterized IFC type counts;
- preservation of all detailed listing fields;
- graph-only routing overrides;
- detection of a partial aggregate result shape;
- exactly one bounded corrective query for an incomplete result;
- parameter propagation and execution diagnostics.

The plans were also executed against the current Neo4j database. They returned the expected two grouped global rows, two grouped storey rows, both zero anomaly counts, both exact IFC-type counts, and complete detailed relationship rows.

## Remaining limitations

- Novel graph questions outside recognized shapes still depend on free-form model generation.
- Completeness validation checks result columns, not the semantic truth of arbitrary free-form expressions.
- Natural-language storey extraction currently targets explicit “on floor/storey” and Persian `در طبقه ... چند` count phrasing; unusual phrasing may use free-form generation.
- List context is capped at 25 displayed rows although the database query may retrieve 50.
- Database counts are mutable and snapshot expectations must be refreshed after ingestion or analysis.
- A future extension can use a fully structured LLM plan schema for novel multi-step graph analytics, with a strict maximum query count and the same column contract.
