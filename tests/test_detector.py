"""Tests for n1detect — N+1 SQL query detection."""
import pytest
from n1detect.collector import QueryCollector, QueryRecord
from n1detect.detector import normalise, detect, N1Finding, DetectionReport
from n1detect.middleware import wrap_connection, n1detect_context, N1DetectedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(sql, ms=1.0):
    return QueryRecord(sql=sql, params=None, duration_ms=ms)


def _queries(sql, n, ms=1.0):
    return [_q(sql, ms) for _ in range(n)]


# ---------------------------------------------------------------------------
# normalise()
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_strips_integer_literals(self):
        assert normalise("SELECT * FROM t WHERE id = 42") == normalise("SELECT * FROM t WHERE id = 99")

    def test_strips_string_literals(self):
        assert normalise("SELECT * FROM t WHERE name = 'alice'") == normalise("SELECT * FROM t WHERE name = 'bob'")

    def test_collapses_whitespace(self):
        assert normalise("SELECT   *  FROM   t") == normalise("SELECT * FROM t")

    def test_uppercases_keywords(self):
        assert normalise("select * from t") == normalise("SELECT * FROM T")

    def test_replaces_question_mark_placeholder(self):
        assert normalise("SELECT * FROM t WHERE id = ?") == normalise("SELECT * FROM t WHERE id = 1")

    def test_replaces_percent_s_placeholder(self):
        assert normalise("SELECT * FROM t WHERE id = %s") == normalise("SELECT * FROM t WHERE id = 1")

    def test_replaces_dollar_placeholder(self):
        assert normalise("SELECT * FROM t WHERE id = $1") == normalise("SELECT * FROM t WHERE id = 1")

    def test_different_tables_differ(self):
        assert normalise("SELECT * FROM users WHERE id = 1") != normalise("SELECT * FROM posts WHERE id = 1")

    def test_different_columns_differ(self):
        assert normalise("SELECT name FROM t WHERE id = 1") != normalise("SELECT email FROM t WHERE id = 1")

    def test_returns_string(self):
        assert isinstance(normalise("SELECT 1"), str)


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestDetect:
    def test_no_queries_returns_empty_report(self):
        report = detect([])
        assert not report.has_findings
        assert report.total_queries == 0

    def test_below_threshold_no_finding(self):
        queries = _queries("SELECT * FROM users WHERE id = 1", 2)
        report = detect(queries, threshold=3)
        assert not report.has_findings

    def test_at_threshold_produces_finding(self):
        queries = _queries("SELECT * FROM users WHERE id = 1", 3)
        report = detect(queries, threshold=3)
        assert report.has_findings
        assert len(report.findings) == 1

    def test_above_threshold_produces_finding(self):
        queries = _queries("SELECT * FROM posts WHERE user_id = 1", 10)
        report = detect(queries, threshold=3)
        assert report.has_findings
        assert report.findings[0].count == 10

    def test_multiple_n1_patterns(self):
        qs = (
            _queries("SELECT * FROM users WHERE id = 1", 5) +
            _queries("SELECT * FROM posts WHERE user_id = 1", 4)
        )
        report = detect(qs, threshold=3)
        assert len(report.findings) == 2

    def test_one_off_queries_not_flagged(self):
        queries = [
            _q("SELECT count(*) FROM users"),
            _q("SELECT * FROM settings"),
        ]
        report = detect(queries, threshold=2)
        assert not report.has_findings

    def test_findings_sorted_by_count_desc(self):
        qs = (
            _queries("SELECT * FROM a WHERE id = 1", 10) +
            _queries("SELECT * FROM b WHERE id = 1", 5)
        )
        report = detect(qs, threshold=3)
        assert report.findings[0].count >= report.findings[1].count

    def test_total_queries_count(self):
        qs = _queries("SELECT * FROM t WHERE id = 1", 5) + [_q("SELECT 1")]
        report = detect(qs, threshold=3)
        assert report.total_queries == 6

    def test_total_ms_summed(self):
        qs = _queries("SELECT * FROM t WHERE id = 1", 3, ms=2.0)
        report = detect(qs, threshold=3)
        assert abs(report.total_ms - 6.0) < 0.001

    def test_finding_avg_ms(self):
        qs = [
            _q("SELECT * FROM t WHERE id = 1", ms=2.0),
            _q("SELECT * FROM t WHERE id = 2", ms=4.0),
            _q("SELECT * FROM t WHERE id = 3", ms=6.0),
        ]
        report = detect(qs, threshold=3)
        assert abs(report.findings[0].avg_ms - 4.0) < 0.01

    def test_custom_threshold(self):
        queries = _queries("SELECT * FROM t WHERE id = 1", 5)
        assert not detect(queries, threshold=10).has_findings
        assert detect(queries, threshold=5).has_findings

    def test_max_examples_limits_stored_records(self):
        queries = _queries("SELECT * FROM t WHERE id = 1", 20)
        report = detect(queries, threshold=3, max_examples=5)
        assert len(report.findings[0].examples) == 5

    def test_string_repr_no_findings(self):
        s = str(detect([], threshold=3))
        assert "No N+1" in s

    def test_string_repr_with_findings(self):
        queries = _queries("SELECT * FROM t WHERE id = 1", 5)
        s = str(detect(queries, threshold=3))
        assert "N+1 report" in s

    def test_finding_str(self):
        queries = _queries("SELECT * FROM t WHERE id = 1", 5)
        report = detect(queries, threshold=3)
        s = str(report.findings[0])
        assert "N+1 detected" in s
        assert "5x" in s


# ---------------------------------------------------------------------------
# QueryCollector
# ---------------------------------------------------------------------------

class TestQueryCollector:
    def test_empty_on_init(self):
        col = QueryCollector()
        assert col.count() == 0

    def test_record_adds_query(self):
        col = QueryCollector()
        col.record("SELECT 1", None, 1.0)
        assert col.count() == 1

    def test_all_queries_returns_list(self):
        col = QueryCollector()
        col.record("SELECT 1", None, 1.0)
        col.record("SELECT 2", None, 2.0)
        assert len(col.all_queries()) == 2

    def test_reset_clears_queries(self):
        col = QueryCollector()
        col.record("SELECT 1", None, 1.0)
        col.reset()
        assert col.count() == 0


# ---------------------------------------------------------------------------
# wrap_connection
# ---------------------------------------------------------------------------

class TestWrapConnection:
    def _sqlite_conn(self):
        import sqlite3
        return sqlite3.connect(":memory:")

    def test_basic_query_recorded(self):
        col = QueryCollector()
        conn = wrap_connection(self._sqlite_conn(), collector=col)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert col.count() == 1

    def test_query_sql_stored(self):
        col = QueryCollector()
        conn = wrap_connection(self._sqlite_conn(), collector=col)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert col.all_queries()[0].sql == "SELECT 1"

    def test_multiple_queries_all_recorded(self):
        col = QueryCollector()
        conn = wrap_connection(self._sqlite_conn(), collector=col)
        cur = conn.cursor()
        cur.execute("CREATE TABLE t (id INTEGER)")
        cur.execute("INSERT INTO t VALUES (1)")
        cur.execute("SELECT * FROM t")
        assert col.count() == 3

    def test_duration_recorded(self):
        col = QueryCollector()
        conn = wrap_connection(self._sqlite_conn(), collector=col)
        conn.cursor().execute("SELECT 1")
        assert col.all_queries()[0].duration_ms >= 0


# ---------------------------------------------------------------------------
# n1detect_context
# ---------------------------------------------------------------------------

class TestN1DetectContext:
    def _make_queries_via_context(self, sql_list, threshold=3):
        import sqlite3
        from n1detect.middleware import get_collector
        col = QueryCollector()
        with n1detect_context(threshold=threshold, collector=col) as c:
            conn = wrap_connection(sqlite3.connect(":memory:"), collector=col)
            cur = conn.cursor()
            for sql in sql_list:
                cur.execute(sql)
        return col

    def test_no_n1_no_finding(self):
        col = QueryCollector()
        with n1detect_context(threshold=3, collector=col):
            col.record("SELECT 1", None, 1.0)
        assert not col.last_report.has_findings

    def test_n1_detected_in_context(self):
        col = QueryCollector()
        with n1detect_context(threshold=3, collector=col):
            for i in range(5):
                col.record(f"SELECT * FROM users WHERE id = {i}", None, 1.0)
        assert col.last_report.has_findings

    def test_raise_on_finding(self):
        col = QueryCollector()
        with pytest.raises(N1DetectedError):
            with n1detect_context(threshold=3, raise_on_finding=True, collector=col):
                for i in range(5):
                    col.record(f"SELECT * FROM posts WHERE user_id = {i}", None, 1.0)

    def test_no_raise_without_flag(self):
        col = QueryCollector()
        with n1detect_context(threshold=3, raise_on_finding=False, collector=col):
            for i in range(5):
                col.record(f"SELECT * FROM posts WHERE user_id = {i}", None, 1.0)
        # Should not raise

    def test_reset_between_contexts(self):
        col = QueryCollector()
        with n1detect_context(threshold=3, collector=col):
            col.record("SELECT 1", None, 1.0)
        with n1detect_context(threshold=3, collector=col):
            pass
        assert col.last_report.total_queries == 0
