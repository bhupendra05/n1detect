"""N+1 detection logic — identifies repeated similar queries."""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from .collector import QueryRecord


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_LITERAL_INT = re.compile(r"\b\d+\b")
_LITERAL_STR = re.compile(r"'[^']*'")
_PARAM_PLACEHOLDER = re.compile(r"\?|%s|%\([^)]+\)s|\$\d+")
_WHITESPACE = re.compile(r"\s+")


def normalise(sql: str) -> str:
    """Strip literals so semantically identical queries compare equal."""
    sql = _LITERAL_STR.sub("'?'", sql)
    sql = _PARAM_PLACEHOLDER.sub("?", sql)
    sql = _LITERAL_INT.sub("?", sql)
    sql = _WHITESPACE.sub(" ", sql).strip().upper()
    return sql


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

@dataclass
class N1Finding:
    pattern: str          # normalised SQL template
    count: int            # how many times it fired
    threshold: int        # the configured threshold
    examples: List[QueryRecord] = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0

    def __str__(self) -> str:
        return (
            f"N+1 detected: query executed {self.count}x "
            f"(threshold={self.threshold}, avg={self.avg_ms:.1f}ms)\n"
            f"  Pattern: {self.pattern}"
        )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

@dataclass
class DetectionReport:
    findings: List[N1Finding] = field(default_factory=list)
    total_queries: int = 0
    total_ms: float = 0.0

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def __str__(self) -> str:
        if not self.findings:
            return f"No N+1 detected. Total queries: {self.total_queries} ({self.total_ms:.1f}ms)"
        lines = [f"N+1 report — {len(self.findings)} pattern(s) detected:"]
        for f in self.findings:
            lines.append(f"  • {f}")
        lines.append(f"Total queries: {self.total_queries} ({self.total_ms:.1f}ms)")
        return "\n".join(lines)


def detect(
    queries: List[QueryRecord],
    threshold: int = 3,
    max_examples: int = 3,
) -> DetectionReport:
    """
    Identify N+1 patterns in a list of recorded queries.

    A pattern is flagged when the same normalised SQL template appears
    >= *threshold* times.
    """
    pattern_counter: Counter = Counter()
    pattern_queries: dict[str, List[QueryRecord]] = {}

    for q in queries:
        key = normalise(q.sql)
        pattern_counter[key] += 1
        pattern_queries.setdefault(key, []).append(q)

    findings: List[N1Finding] = []
    for pattern, count in pattern_counter.items():
        if count >= threshold:
            recs = pattern_queries[pattern]
            findings.append(
                N1Finding(
                    pattern=pattern,
                    count=count,
                    threshold=threshold,
                    examples=recs[:max_examples],
                    total_ms=sum(r.duration_ms for r in recs),
                )
            )

    findings.sort(key=lambda f: f.count, reverse=True)

    return DetectionReport(
        findings=findings,
        total_queries=len(queries),
        total_ms=sum(q.duration_ms for q in queries),
    )
