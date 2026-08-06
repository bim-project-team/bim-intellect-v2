"""
config.py
---------
Single place for connection settings and file paths, so every script in the
pipeline (loader, clash detector, write-back) imports from here instead of
hardcoding values. Override with environment variables in production;
defaults match the docker-compose.yml in this project.
"""

import os

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "bimintellect")

NODES_CSV = os.environ.get("NODES_CSV", "nodes.csv")
EDGES_CSV = os.environ.get("EDGES_CSV", "edges.csv")

# Default IFC file if the API caller doesn't specify one.
DEFAULT_IFC_PATH = os.environ.get("IFC_PATH", "dataset/210_King_Merged.ifc")

BATCH_SIZE = 1000  # rows per UNWIND transaction
