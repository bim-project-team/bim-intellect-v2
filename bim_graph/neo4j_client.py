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
