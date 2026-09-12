"""Shared process-local lock for graph-replacing and graph-derived analyses."""

import threading

PIPELINE_LOCK = threading.RLock()
