"""One-off migration: backfill a `tag` property on every Element node.

Problem this solves: the numeric ID users actually refer to elements by
(e.g. "wall 817660") only exists today as a trailing segment inside the
`name` string (e.g. "Basic Wall:MockUp Storage Wall:817660"). Every
consumer - the LLM, Cypher templates, humans in Neo4j Browser - has to
re-derive it with CONTAINS/regex matching. Storing it as a first-class,
indexed property removes an entire category of "the LLM guessed the
wrong property" bugs (see CYPHER_ACCURACY_ROADMAP.md, Section 3.1).

Run once after this code lands, and again any time new elements are
ingested without going through an updated ingestion pipeline that sets
`tag` at creation time:

    python -m bim_graph.migrate_add_tags

Safe to re-run: only touches nodes where `tag IS NULL`, and does
nothing if there's nothing left to backfill.
"""
import argparse
import logging
import re

from bim_graph.neo4j_client import Neo4jClient

logger = logging.getLogger("bim_intellect.migrate_add_tags")
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

_TAG_PATTERN = re.compile(r":(\d+)$")

BATCH_SIZE = 500


def extract_element_tag(name: str) -> str | None:
    """Pull the trailing numeric Revit/IFC tag out of a name like
    'Basic Wall:MockUp Storage Wall:817660' -> '817660'.
    Returns None if the name doesn't end in a numeric segment."""
    if not name:
        return None
    match = _TAG_PATTERN.search(name)
    return match.group(1) if match else None


def create_tag_index(client: Neo4jClient) -> None:
    client.run(
        "CREATE INDEX element_tag_idx IF NOT EXISTS "
        "FOR (e:Element) ON (e.tag)"
    )
    logger.info("Ensured index element_tag_idx on :Element(tag).")


def fetch_untagged_batch(client: Neo4jClient, batch_size: int) -> list[dict]:
    return client.run(
        "MATCH (e:Element) WHERE e.tag IS NULL AND e.name IS NOT NULL "
        "RETURN elementId(e) AS neo4j_id, e.name AS name "
        "LIMIT $batch_size",
        {"batch_size": batch_size},
    )


def apply_tags(client: Neo4jClient, rows: list[dict]) -> int:
    """Set e.tag for each row that yields a parseable tag. Rows whose
    name has no trailing numeric segment are left untouched (not every
    element has one, and that's expected - e.g. IfcSpace nodes)."""
    updates = [
        {"neo4j_id": row["neo4j_id"], "tag": tag}
        for row in rows
        if (tag := extract_element_tag(row["name"])) is not None
    ]
    if not updates:
        return 0

    client.run(
        "UNWIND $updates AS u "
        "MATCH (e) WHERE elementId(e) = u.neo4j_id "
        "SET e.tag = u.tag",
        {"updates": updates},
    )
    return len(updates)


def run_migration(batch_size: int = BATCH_SIZE) -> None:
    total_tagged = 0
    with Neo4jClient() as client:
        create_tag_index(client)
        while True:
            batch = fetch_untagged_batch(client, batch_size)
            if not batch:
                break
            tagged = apply_tags(client, batch)
            total_tagged += tagged
            logger.info(
                "Processed batch of %d node(s), tagged %d.", len(batch), tagged
            )
            if tagged == 0:
                # Every row in this batch had an unparseable name - avoid
                # an infinite loop since they'll never satisfy `tag IS NULL`
                # unless we also mark them as "checked". Simplest safe fix:
                # stop here and report the remainder for manual review.
                remaining = client.run(
                    "MATCH (e:Element) WHERE e.tag IS NULL AND e.name IS NOT NULL "
                    "RETURN count(e) AS c"
                )[0]["c"]
                if remaining:
                    logger.warning(
                        "%d element(s) have a name with no parseable trailing "
                        "numeric tag and were left untagged (expected for "
                        "types like IfcSpace that aren't referenced by a "
                        "bare numeric ID).",
                        remaining,
                    )
                break

    logger.info("Migration complete. Total nodes tagged: %d", total_tagged)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help="Number of nodes to process per batch (default: %(default)s)",
    )
    args = parser.parse_args()
    run_migration(batch_size=args.batch_size)
