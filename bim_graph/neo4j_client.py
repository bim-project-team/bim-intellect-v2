"""
neo4j_client.py
----------------
Thin wrapper around the official neo4j Python driver so every other script
connects the same way and closes its connection properly (via context
manager) instead of each script rolling its own driver setup.

Install once:
    pip install neo4j
"""

from neo4j import GraphDatabase
from bim_graph.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


class Neo4jClient:
    def __init__(self, uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def verify_connectivity(self):
        self._driver.verify_connectivity()

    def run(self, query, parameters=None):
        """Run a single query, return all records as a list of dicts."""
        with self._driver.session() as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def validate(self, query: str, parameters=None) -> tuple[bool, list[str]]:
        """Check a query's validity/planner warnings via EXPLAIN, without
        executing it against real data.

        Returns (is_valid, warning_messages). is_valid=False means the
        query has a syntax/semantic error (e.g. unknown procedure) and
        was never planned. is_valid=True with non-empty warnings means
        the query planned successfully but Neo4j flagged something
        suspicious - e.g. "the property `id` is never used", which is
        exactly the shape of warning a wrong tag-vs-id match would
        produce. Callers should log these warnings rather than treat
        them as fatal, since some are benign (e.g. unused label hints).
        """
        try:
            with self._driver.session() as session:
                result = session.run(f"EXPLAIN {query}", parameters or {})
                summary = result.consume()
                warnings = []
                for notification in summary.notifications or []:
                    description = (
                        notification.get("description", "")
                        if isinstance(notification, dict)
                        else getattr(notification, "description", "")
                    )
                    if description:
                        warnings.append(description)
                return True, warnings
        except Exception as exc:
            return False, [str(exc)]

    def count_label(self, label: str) -> int:
        """Return the total number of nodes with the given label. Used to
        distinguish 'the filter is wrong' (label has nodes, none matched)
        from 'there's genuinely no data' (label is empty) when a filtered
        query returns zero rows. `label` must come from a value already
        validated against known schema labels (e.g. extracted from a
        generated Cypher string), never directly from unsanitized user
        input, since it's interpolated into the query.
        """
        result = self.run(f"MATCH (n:{label}) RETURN count(n) AS c")
        return result[0]["c"] if result else 0

    def run_batched(self, query, rows, batch_size):
        """
        Run `query` repeatedly with `UNWIND $rows AS row` semantics, splitting
        `rows` into chunks of `batch_size` so a single transaction never holds
        an enormous payload. `query` must reference `$rows` as its UNWIND
        source, e.g.:
            UNWIND $rows AS row
            MERGE (e:Element {id: row.id}) ...
        """
        with self._driver.session() as session:
            for i in range(0, len(rows), batch_size):
                chunk = rows[i:i + batch_size]
                session.run(query, rows=chunk)
