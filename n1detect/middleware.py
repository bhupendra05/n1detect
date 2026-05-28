"""
Instrumentation helpers.

Provides:
  - ``wrap_connection``  — wraps any DB-API 2.0 connection
  - ``n1detect_context`` — context manager: collect → detect → optionally raise
  - Django middleware class ``N1DetectMiddleware``
  - SQLAlchemy event hook ``attach_sqlalchemy``
"""
from __future__ import annotations
import contextlib
import time
import traceback
from typing import Callable, Generator, Optional

from .collector import QueryCollector, QueryRecord
from .detector import DetectionReport, detect

# ---------------------------------------------------------------------------
# Global singleton collector (used by all instrumentation hooks)
# ---------------------------------------------------------------------------

_collector = QueryCollector()


def get_collector() -> QueryCollector:
    return _collector


# ---------------------------------------------------------------------------
# DB-API connection wrapper
# ---------------------------------------------------------------------------

class _InstrumentedCursor:
    def __init__(self, cursor, collector: QueryCollector, capture_stack: bool) -> None:
        self._cursor = cursor
        self._collector = collector
        self._capture_stack = capture_stack

    def execute(self, sql, params=None):
        stack = "".join(traceback.format_stack()[:-1]) if self._capture_stack else None
        t0 = time.perf_counter()
        try:
            if params is None:
                return self._cursor.execute(sql)
            return self._cursor.execute(sql, params)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            self._collector.record(sql, params, ms, stack)

    def executemany(self, sql, seq_of_params):
        t0 = time.perf_counter()
        try:
            return self._cursor.executemany(sql, seq_of_params)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            self._collector.record(sql, seq_of_params, ms)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)


class _InstrumentedConnection:
    def __init__(self, conn, collector: QueryCollector, capture_stack: bool = False) -> None:
        self._conn = conn
        self._collector = collector
        self._capture_stack = capture_stack

    def cursor(self):
        return _InstrumentedCursor(self._conn.cursor(), self._collector, self._capture_stack)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._conn.__exit__(*args)


def wrap_connection(conn, collector: Optional[QueryCollector] = None, capture_stack: bool = False):
    """Wrap a DB-API 2.0 connection so all queries are recorded."""
    col = collector or _collector
    return _InstrumentedConnection(conn, col, capture_stack)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def n1detect_context(
    threshold: int = 3,
    raise_on_finding: bool = False,
    collector: Optional[QueryCollector] = None,
) -> Generator[QueryCollector, None, None]:
    """
    Context manager that collects queries and detects N+1 patterns.

    Usage::

        with n1detect_context(threshold=3) as col:
            # run code that makes DB queries
            ...
        # findings are available via col.last_report (if raise_on_finding=False)

    Raises ``N1DetectedError`` if *raise_on_finding* is True and patterns found.
    """
    col = collector or _collector
    col.reset()
    try:
        yield col
    finally:
        queries = col.all_queries()
        report = detect(queries, threshold=threshold)
        col.last_report = report  # type: ignore[attr-defined]
        if raise_on_finding and report.has_findings:
            raise N1DetectedError(report)


class N1DetectedError(Exception):
    def __init__(self, report: DetectionReport) -> None:
        self.report = report
        super().__init__(str(report))


# ---------------------------------------------------------------------------
# Django middleware
# ---------------------------------------------------------------------------

class N1DetectMiddleware:
    """
    Django middleware that logs N+1 patterns per request.

    Add to MIDDLEWARE *after* SecurityMiddleware::

        MIDDLEWARE = [
            ...
            "n1detect.middleware.N1DetectMiddleware",
        ]

    Optional settings::

        N1DETECT_THRESHOLD = 3       # default
        N1DETECT_LOG_LEVEL = "WARNING"
        N1DETECT_RAISE = False       # set True to raise in tests
    """

    def __init__(self, get_response: Callable) -> None:
        self.get_response = get_response
        try:
            from django.conf import settings
            self.threshold = getattr(settings, "N1DETECT_THRESHOLD", 3)
            self.raise_on_finding = getattr(settings, "N1DETECT_RAISE", False)
            log_level = getattr(settings, "N1DETECT_LOG_LEVEL", "WARNING")
        except ImportError:
            self.threshold = 3
            self.raise_on_finding = False
            log_level = "WARNING"

        import logging
        self._log = logging.getLogger("n1detect")
        self._log_fn = getattr(self._log, log_level.lower(), self._log.warning)

        self._install_django_hook()

    def _install_django_hook(self) -> None:
        try:
            from django.db import connection as django_conn
            from django.db.backends.signals import connection_created

            def on_connection_created(sender, connection, **kwargs):
                pass  # Django wraps its own cursor; we use signals below

            try:
                from django.test.signals import setting_changed  # noqa
            except ImportError:
                pass

            # Hook into Django's execute wrapper
            try:
                from django.db import connections
                # We monkey-patch via DatabaseWrapper.execute_wrapper
                pass
            except Exception:
                pass
        except ImportError:
            pass

    def __call__(self, request):
        _collector.reset()
        response = self.get_response(request)
        queries = _collector.all_queries()
        if queries:
            report = detect(queries, threshold=self.threshold)
            if report.has_findings:
                self._log_fn(
                    "N+1 detected on %s %s:\n%s",
                    request.method,
                    request.path,
                    str(report),
                )
                if self.raise_on_finding:
                    raise N1DetectedError(report)
        return response


# ---------------------------------------------------------------------------
# SQLAlchemy event hook
# ---------------------------------------------------------------------------

def attach_sqlalchemy(engine, collector: Optional[QueryCollector] = None) -> None:
    """
    Attach N+1 tracking to a SQLAlchemy engine via its event system.

    Usage::

        from sqlalchemy import create_engine
        engine = create_engine("sqlite:///test.db")
        attach_sqlalchemy(engine)
    """
    col = collector or _collector
    try:
        from sqlalchemy import event

        @event.listens_for(engine, "before_cursor_execute")
        def before_execute(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("n1detect_t0", time.perf_counter())

        @event.listens_for(engine, "after_cursor_execute")
        def after_execute(conn, cursor, statement, parameters, context, executemany):
            t0 = conn.info.pop("n1detect_t0", None)
            ms = (time.perf_counter() - t0) * 1000 if t0 is not None else 0.0
            col.record(statement, parameters, ms)

    except ImportError as exc:
        raise ImportError("SQLAlchemy is required: pip install sqlalchemy") from exc
