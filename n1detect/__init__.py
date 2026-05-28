"""n1detect — N+1 SQL query detector for Python ORMs."""
from .collector import QueryCollector, QueryRecord
from .detector import detect, DetectionReport, N1Finding, normalise
from .middleware import (
    wrap_connection,
    n1detect_context,
    N1DetectedError,
    attach_sqlalchemy,
    get_collector,
)

__all__ = [
    "QueryCollector",
    "QueryRecord",
    "detect",
    "DetectionReport",
    "N1Finding",
    "normalise",
    "wrap_connection",
    "n1detect_context",
    "N1DetectedError",
    "attach_sqlalchemy",
    "get_collector",
]
__version__ = "0.1.0"
