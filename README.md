# n1detect

**Detect N+1 SQL query patterns in Django, SQLAlchemy, or any Python app.**

N+1 is the most common ORM performance bug: your code fires 1 query to fetch a list, then N more queries to enrich each row. `n1detect` spots it automatically — in tests, in CI, or from a log file.

```bash
pip install n1detect
```

[![CI](https://github.com/bhupendra05/n1detect/actions/workflows/ci.yml/badge.svg)](https://github.com/bhupendra05/n1detect/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What is N+1?

```python
# This innocent-looking loop is N+1:
users = User.objects.all()          # 1 query
for user in users:
    print(user.profile.bio)         # N queries (one per user)

# Fix: prefetch_related / select_related / joinedload
```

---

## Usage

### Context manager (any framework)

```python
from n1detect import n1detect_context, wrap_connection
import sqlite3

conn = sqlite3.connect("mydb.db")
col = None

from n1detect import QueryCollector
col = QueryCollector()
wrapped = wrap_connection(conn, collector=col)

with n1detect_context(threshold=3, collector=col) as c:
    cur = wrapped.cursor()
    for user_id in range(100):
        cur.execute("SELECT * FROM posts WHERE user_id = ?", (user_id,))

print(col.last_report)
# N+1 report — 1 pattern(s) detected:
#   • N+1 detected: query executed 100x (threshold=3, avg=0.3ms)
#       Pattern: SELECT * FROM POSTS WHERE USER_ID = ?
```

### Raise in tests

```python
with n1detect_context(threshold=3, raise_on_finding=True):
    response = client.get("/api/users/")  # raises N1DetectedError if N+1 found
```

### Wrap any DB-API 2.0 connection

```python
from n1detect import wrap_connection, QueryCollector, detect

col = QueryCollector()
conn = wrap_connection(sqlite3.connect(":memory:"), collector=col)

# ... run your queries ...

report = detect(col.all_queries(), threshold=3)
print(report)
```

### SQLAlchemy

```python
from sqlalchemy import create_engine
from n1detect import attach_sqlalchemy, QueryCollector, detect

engine = create_engine("sqlite:///mydb.db")
col = QueryCollector()
attach_sqlalchemy(engine, collector=col)

# ... run session queries ...

report = detect(col.all_queries(), threshold=3)
```

### Django middleware

```python
# settings.py
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "n1detect.middleware.N1DetectMiddleware",  # add here
    ...
]

N1DETECT_THRESHOLD = 3       # flag queries repeated >= 3 times
N1DETECT_LOG_LEVEL = "WARNING"
N1DETECT_RAISE = False       # set True in test settings to fail fast
```

### Analyse a SQL log file

```bash
# Plain SQL log (one query per line)
n1detect analyse queries.log --threshold 3

# JSON output (Django Debug Toolbar format supported)
n1detect analyse queries.log --json

# Fail CI if patterns found
n1detect analyse queries.log --fail-on-findings
```

---

## How it works

1. **Intercept** — wraps DB-API cursors or SQLAlchemy events to record every SQL statement + execution time
2. **Normalise** — strips integer/string literals and parameter placeholders so `WHERE id = 1` and `WHERE id = 2` are the same template
3. **Count** — groups by normalised template; flags any group that exceeds the threshold
4. **Report** — shows count, total time, avg time, and example queries per pattern

---

## Python API

```python
from n1detect import detect, QueryRecord

queries = [QueryRecord(sql="SELECT * FROM posts WHERE user_id = 1", params=None, duration_ms=2.1)] * 50

report = detect(queries, threshold=3)
print(report.has_findings)  # True
for finding in report.findings:
    print(f"{finding.count}x — {finding.pattern[:60]} ({finding.avg_ms:.1f}ms avg)")
```

---

## License

MIT © [Bhupendra Tale](https://github.com/bhupendra05)
