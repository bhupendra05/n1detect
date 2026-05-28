"""Query collector — intercepts SQL statements via a connection wrapper."""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QueryRecord:
    sql: str
    params: object
    duration_ms: float
    stack: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class QueryCollector:
    """Thread-local query collector that wraps a DB-API 2.0 connection."""

    def __init__(self, capture_stack: bool = False) -> None:
        self._local = threading.local()
        self.capture_stack = capture_stack

    # ------------------------------------------------------------------ #
    # Internal state per thread
    # ------------------------------------------------------------------ #

    @property
    def _queries(self) -> List[QueryRecord]:
        if not hasattr(self._local, "queries"):
            self._local.queries = []
        return self._local.queries

    def record(self, sql: str, params: object, duration_ms: float, stack: Optional[str] = None) -> None:
        self._queries.append(QueryRecord(sql=sql, params=params, duration_ms=duration_ms, stack=stack))

    def reset(self) -> None:
        self._local.queries = []

    def all_queries(self) -> List[QueryRecord]:
        return list(self._queries)

    def count(self) -> int:
        return len(self._queries)
