"""CLI for n1detect — analyse a SQLite query log file."""
from __future__ import annotations
import json
import re
import sys
import click
from .collector import QueryRecord
from .detector import detect


def _parse_log(path: str):
    """
    Parse a newline-delimited log file of SQL statements.

    Supports formats:
      • Plain SQL lines
      • Django debug toolbar JSON dump (list of {sql, time} objects)
      • Lines like: [12.3ms] SELECT ...
    """
    records = []
    _ms_prefix = re.compile(r"^\[(\d+(?:\.\d+)?)ms\]\s*(.+)")
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()

    # Try JSON
    try:
        data = json.loads(content)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    sql = item.get("sql", item.get("statement", ""))
                    ms = float(item.get("time", item.get("duration_ms", 0)))
                    if sql:
                        records.append(QueryRecord(sql=sql, params=None, duration_ms=ms))
            return records
    except (json.JSONDecodeError, TypeError):
        pass

    # Line-by-line
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ms_prefix.match(line)
        if m:
            records.append(QueryRecord(sql=m.group(2), params=None, duration_ms=float(m.group(1))))
        else:
            records.append(QueryRecord(sql=line, params=None, duration_ms=0.0))

    return records


@click.group()
def cli() -> None:
    """n1detect — detect N+1 SQL query patterns."""


@cli.command("analyse")
@click.argument("log_file", type=click.Path(exists=True))
@click.option("--threshold", "-t", default=3, show_default=True, help="Repeat count to flag as N+1.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.option("--fail-on-findings", is_flag=True, help="Exit 1 if N+1 patterns found.")
def analyse(log_file: str, threshold: int, as_json: bool, fail_on_findings: bool) -> None:
    """Analyse a SQL log file for N+1 patterns."""
    records = _parse_log(log_file)
    report = detect(records, threshold=threshold)

    if as_json:
        click.echo(json.dumps({
            "total_queries": report.total_queries,
            "total_ms": round(report.total_ms, 2),
            "findings": [
                {
                    "pattern": f.pattern,
                    "count": f.count,
                    "total_ms": round(f.total_ms, 2),
                    "avg_ms": round(f.avg_ms, 2),
                }
                for f in report.findings
            ],
        }, indent=2))
    else:
        click.echo(f"\nn1detect — analysing: {log_file}")
        click.echo(f"Total queries: {report.total_queries}  |  Total time: {report.total_ms:.1f}ms\n")
        if not report.findings:
            click.echo(click.style("  No N+1 patterns detected.", fg="green"))
        else:
            click.echo(click.style(f"  {len(report.findings)} N+1 pattern(s) found:\n", fg="red"))
            for i, finding in enumerate(report.findings, 1):
                click.echo(click.style(f"  [{i}] Executed {finding.count}x  (avg {finding.avg_ms:.1f}ms)", fg="yellow"))
                click.echo(f"      Pattern: {finding.pattern[:120]}")
        click.echo()

    if fail_on_findings and report.has_findings:
        sys.exit(1)


@cli.command("demo")
def demo() -> None:
    """Show a quick demo of N+1 detection with synthetic data."""
    from .collector import QueryRecord
    queries = (
        [QueryRecord(sql="SELECT * FROM users WHERE id = 1", params=None, duration_ms=2.1)] +
        [QueryRecord(sql=f"SELECT * FROM posts WHERE user_id = {i}", params=None, duration_ms=1.5)
         for i in range(10)] +
        [QueryRecord(sql="SELECT count(*) FROM comments", params=None, duration_ms=5.0)]
    )
    report = detect(queries, threshold=3)
    click.echo("\nn1detect demo\n")
    click.echo(str(report))
    click.echo()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
