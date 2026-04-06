#!/usr/bin/env python3
"""Golden Query Runner — Execute validated KQL with Data Card transparency.

Usage:
    # Run a golden query by ID
    python scripts/kusto_runner.py --query-id GQ-001

    # Run ad-hoc KQL
    python scripts/kusto_runner.py --kql "TenantCatalogSnapshot | take 5" \
        --cluster https://xdeployment.kusto.windows.net --db Deployment

    # Validate a golden query (dry-run + schema check)
    python scripts/kusto_runner.py --query-id GQ-001 --validate

    # List all registered golden queries
    python scripts/kusto_runner.py --list

    # Output as JSON (for programmatic use)
    python scripts/kusto_runner.py --query-id GQ-001 --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_GOLDEN_REGISTRY = _REPO_ROOT / "state" / "work" / "golden-queries.md"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GoldenQuery:
    """A registered golden query with metadata."""
    id: str
    title: str
    category: str
    question: str
    cluster: str
    database: str
    table: str
    kql: str
    freshness_description: str
    confidence: str  # HIGH, MEDIUM, LOW
    validated_date: str
    last_known: str = ""
    caveats: list[str] = field(default_factory=list)


@dataclass
class DataCard:
    """Transparency card attached to every query result."""
    query_id: str
    title: str
    cluster: str
    database: str
    table: str
    kql_preview: str
    data_freshness: str
    confidence: str
    confidence_symbol: str
    caveats: list[str]
    execution_time_sec: float
    row_count: int
    timestamp_utc: str

    def render(self) -> str:
        caveats_str = "; ".join(self.caveats) if self.caveats else "None"
        return (
            f"\n📊 Data Card\n"
            f"{'─' * 45}\n"
            f"Query:      {self.query_id} — {self.title}\n"
            f"Source:     {self.cluster}/{self.database}.{self.table}\n"
            f"KQL:        {self.kql_preview}\n"
            f"Freshness:  {self.data_freshness}\n"
            f"Confidence: {self.confidence} {self.confidence_symbol}\n"
            f"Rows:       {self.row_count}\n"
            f"Exec time:  {self.execution_time_sec:.1f}s\n"
            f"Caveats:    {caveats_str}\n"
            f"Run at:     {self.timestamp_utc}\n"
            f"{'─' * 45}"
        )


# ---------------------------------------------------------------------------
# Registry parser
# ---------------------------------------------------------------------------

def parse_registry(path: Path = _GOLDEN_REGISTRY) -> dict[str, GoldenQuery]:
    """Parse golden-queries.md into GoldenQuery objects."""
    if not path.exists():
        print(f"ERROR: Registry not found at {path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    queries: dict[str, GoldenQuery] = {}

    # Split by ### GQ- headers
    sections = re.split(r"(?=^### GQ-)", text, flags=re.MULTILINE)

    for section in sections:
        m = re.match(r"^### (GQ-\d+): (.+)", section)
        if not m:
            continue

        qid = m.group(1)
        title = m.group(2).strip()

        def _extract_field(name: str) -> str:
            pattern = rf"\*\*{re.escape(name)}\*\*\s*\|\s*(.+)"
            fm = re.search(pattern, section)
            if not fm:
                return ""
            val = fm.group(1).strip().rstrip("|").strip()
            return val

        category = _extract_field("Category")
        question = _extract_field("Question")
        cluster = _extract_field("Cluster").strip("`")
        database = _extract_field("Database").strip("`")
        table = _extract_field("Table").strip("`")
        freshness = _extract_field("Freshness")
        confidence_raw = _extract_field("Confidence")
        validated = _extract_field("Validated")
        last_known = _extract_field("Last Known")

        # Extract confidence level
        confidence = "MEDIUM"
        if "HIGH" in confidence_raw:
            confidence = "HIGH"
        elif "LOW" in confidence_raw:
            confidence = "LOW"

        # Extract KQL block
        kql_match = re.search(r"```kql\s*\n(.+?)```", section, re.DOTALL)
        kql = kql_match.group(1).strip() if kql_match else ""

        # Extract caveats from validation notes
        caveats = []
        for cm in re.finditer(r"⚠️\s*(.+)", section):
            caveats.append(cm.group(1).strip())
        for cm in re.finditer(r"⛔\s*(.+)", section):
            caveats.append(cm.group(1).strip())

        queries[qid] = GoldenQuery(
            id=qid,
            title=title,
            category=category,
            question=question,
            cluster=cluster,
            database=database,
            table=table,
            kql=kql,
            freshness_description=freshness,
            confidence=confidence,
            validated_date=validated,
            last_known=last_known,
            caveats=caveats,
        )

    return queries


# ---------------------------------------------------------------------------
# KQL normalizer (handles let-statement SDK quirk)
# ---------------------------------------------------------------------------

def normalize_kql(kql: str) -> str:
    """Normalize KQL for the Python SDK.

    The azure-kusto-data SDK can fail on multi-statement queries (with `let`)
    if there are formatting issues. This function:
    1. Strips comment lines (// ...)
    2. Collapses to minimal whitespace
    3. Ensures `let` statements are semicolon-terminated
    4. Removes trailing semicolons from the final expression
    """
    lines = []
    for line in kql.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if stripped:
            lines.append(stripped)

    # Join with newline (SDK handles newlines fine, just not leading whitespace)
    normalized = "\n".join(lines)

    # Ensure let statements end with semicolons
    # Pattern: `let <name> = <expr>` not followed by `;`
    normalized = re.sub(
        r"(let\s+\w+\s*=\s*[^;]+?)(\n(?=let |[A-Z]))",
        r"\1;\2",
        normalized,
    )

    # Remove trailing semicolon from last line
    normalized = normalized.rstrip().rstrip(";")

    return normalized


# ---------------------------------------------------------------------------
# Kusto executor
# ---------------------------------------------------------------------------

class KustoRunner:
    """Execute KQL queries against Azure Data Explorer clusters."""

    def __init__(self):
        self._clients: dict[str, object] = {}

    def _get_client(self, cluster_uri: str):
        """Get or create a Kusto client for the given cluster."""
        if cluster_uri not in self._clients:
            try:
                from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:
                raise ImportError(
                    "azure-kusto-data not installed. "
                    "Run: pip install azure-kusto-data azure-identity"
                ) from exc

            cred = DefaultAzureCredential()
            kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
                cluster_uri, cred
            )
            self._clients[cluster_uri] = KustoClient(kcsb)

        return self._clients[cluster_uri]

    def execute(
        self,
        cluster_uri: str,
        database: str,
        kql: str,
        timeout_sec: int = 120,
    ) -> tuple[list[dict], float]:
        """Execute KQL and return (rows_as_dicts, elapsed_seconds)."""
        client = self._get_client(cluster_uri)
        normalized = normalize_kql(kql)

        start = time.time()
        try:
            from azure.kusto.data import ClientRequestProperties
            from datetime import timedelta as _td
            props = ClientRequestProperties()
            props.set_option("servertimeout", _td(seconds=timeout_sec))

            response = client.execute(database, normalized, properties=props)
        except Exception as e:
            elapsed = time.time() - start
            print(f"ERROR: KQL execution failed after {elapsed:.1f}s: {e}", file=sys.stderr)
            raise

        elapsed = time.time() - start

        rows = []
        primary = response.primary_results[0] if response.primary_results else None
        if primary:
            columns = [col.column_name for col in primary.columns]
            for row in primary:
                rows.append({col: row[col] for col in columns})

        return rows, elapsed

    def detect_freshness(self, rows: list[dict]) -> str:
        """Detect data freshness from TIMESTAMP or date columns."""
        ts_columns = ["TIMESTAMP", "timestamp", "SnapshotDate", "FinishDate", "LastUpdated"]
        now = datetime.now(timezone.utc)

        for col in ts_columns:
            values = [r.get(col) for r in rows if r.get(col) is not None]
            if not values:
                continue

            # Try to find max timestamp
            max_ts = None
            for v in values:
                if isinstance(v, datetime):
                    max_ts = max(max_ts, v) if max_ts else v
                elif isinstance(v, str):
                    try:
                        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                        max_ts = max(max_ts, dt) if max_ts else dt
                    except ValueError:
                        pass

            if max_ts:
                if max_ts.tzinfo is None:
                    max_ts = max_ts.replace(tzinfo=timezone.utc)
                delta = now - max_ts
                hours = delta.total_seconds() / 3600
                if hours < 1:
                    return f"{max_ts.strftime('%Y-%m-%d %H:%M')} UTC ({int(delta.total_seconds() / 60)}m ago)"
                elif hours < 48:
                    return f"{max_ts.strftime('%Y-%m-%d %H:%M')} UTC ({hours:.1f}h ago)"
                else:
                    return f"{max_ts.strftime('%Y-%m-%d')} ({delta.days}d ago)"

        return "Unknown (no timestamp column in results)"

    def run_golden_query(
        self, query: GoldenQuery, validate_only: bool = False
    ) -> tuple[list[dict], DataCard]:
        """Execute a golden query and return results + data card."""
        confidence_symbols = {"HIGH": "✅", "MEDIUM": "⚠️", "LOW": "⛔"}

        if validate_only:
            # Just check connectivity and schema
            test_kql = f"{query.table} | take 1"
            rows, elapsed = self.execute(query.cluster, query.database, test_kql)
            freshness = "Validation only"
        else:
            rows, elapsed = self.execute(query.cluster, query.database, query.kql)
            freshness = self.detect_freshness(rows)

        # First meaningful line of KQL for preview
        kql_lines = [
            l.strip() for l in query.kql.splitlines()
            if l.strip() and not l.strip().startswith("//")
        ]
        kql_preview = kql_lines[0][:80] + "..." if kql_lines else query.kql[:80]

        card = DataCard(
            query_id=query.id,
            title=query.title,
            cluster=query.cluster.replace("https://", "").replace(".kusto.windows.net", ""),
            database=query.database,
            table=query.table,
            kql_preview=kql_preview,
            data_freshness=freshness if not validate_only else freshness,
            confidence=query.confidence,
            confidence_symbol=confidence_symbols.get(query.confidence, "❓"),
            caveats=query.caveats,
            execution_time_sec=elapsed,
            row_count=len(rows),
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        return rows, card


# ---------------------------------------------------------------------------
# Batch Refresh API (for work_loop.py integration)
# ---------------------------------------------------------------------------

# Default set of golden queries to run during /work refresh
REFRESH_QUERY_IDS = [
    "GQ-001",  # Fleet size (clusters/tenants/DCs/nodes)
    "GQ-002",  # Fleet by generation
    "GQ-010",  # Deployment throughput (7d)
    "GQ-012",  # Deployment velocity P50/P75/P90
    "GQ-050",  # Active Sev 0-2 incidents
    "GQ-051",  # Incident volume by severity (trend)
]


def run_refresh_set(
    query_ids: list[str] | None = None,
    registry_path: str | None = None,
    timeout_per_query: int = 15,
) -> dict[str, dict]:
    """
    Run a batch of golden queries and return structured results.

    Returns dict keyed by query ID:
        {"GQ-001": {"rows": [...], "card": DataCard, "error": None}, ...}

    Used by work_loop.py to populate xpf-program-structure.md metrics.
    Each query failure is isolated — other queries still run.
    """
    ids = query_ids or REFRESH_QUERY_IDS
    reg = registry_path or str(_GOLDEN_REGISTRY)
    registry = parse_registry(reg)
    runner = KustoRunner()
    results: dict[str, dict] = {}

    for qid in ids:
        query = registry.get(qid)
        if not query:
            results[qid] = {"rows": [], "card": None, "error": f"Not found in registry"}
            continue
        try:
            rows, card = runner.run_golden_query(query)
            results[qid] = {"rows": rows, "card": card, "error": None}
        except Exception as exc:
            results[qid] = {"rows": [], "card": None, "error": str(exc)[:200]}

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_table(rows: list[dict], max_col_width: int = 40) -> str:
    """Simple ASCII table formatter."""
    if not rows:
        return "(no results)"

    cols = list(rows[0].keys())
    widths = {c: len(c) for c in cols}
    str_rows = []
    for r in rows:
        sr = {}
        for c in cols:
            v = str(r.get(c, ""))
            if len(v) > max_col_width:
                v = v[:max_col_width - 3] + "..."
            sr[c] = v
            widths[c] = max(widths[c], len(v))
        str_rows.append(sr)

    # Header
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines = [header, sep]
    for sr in str_rows[:50]:  # Cap at 50 rows for terminal display
        lines.append(" | ".join(sr.get(c, "").ljust(widths[c]) for c in cols))

    if len(rows) > 50:
        lines.append(f"... ({len(rows) - 50} more rows)")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Golden Query Runner — validated KQL with Data Card transparency"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--query-id", help="Golden query ID (e.g., GQ-001)")
    group.add_argument("--kql", help="Ad-hoc KQL to execute")
    group.add_argument("--list", action="store_true", help="List all registered golden queries")

    parser.add_argument("--cluster", help="Cluster URI (required for --kql)")
    parser.add_argument("--db", help="Database name (required for --kql)")
    parser.add_argument("--validate", action="store_true", help="Validate only (schema check)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--timeout", type=int, default=120, help="Query timeout in seconds")
    parser.add_argument("--registry", type=str, default=str(_GOLDEN_REGISTRY),
                        help="Path to golden-queries.md")

    args = parser.parse_args()

    # List mode
    if args.list:
        queries = parse_registry(Path(args.registry))
        print(f"\n📋 Golden Query Registry ({len(queries)} queries)\n")
        print(f"{'ID':<8} {'Confidence':<10} {'Category':<20} {'Question'}")
        print(f"{'─'*8} {'─'*10} {'─'*20} {'─'*50}")
        for qid in sorted(queries.keys()):
            q = queries[qid]
            sym = {"HIGH": "✅", "MEDIUM": "⚠️", "LOW": "⛔"}.get(q.confidence, "❓")
            print(f"{qid:<8} {sym} {q.confidence:<7} {q.category:<20} {q.question[:50]}")
        return

    runner = KustoRunner()

    # Golden query mode
    if args.query_id:
        queries = parse_registry(Path(args.registry))
        qid = args.query_id.upper()
        if qid not in queries:
            print(f"ERROR: {qid} not found in registry. Use --list to see available queries.",
                  file=sys.stderr)
            sys.exit(1)

        query = queries[qid]
        print(f"⏳ Running {qid}: {query.title}...")
        print(f"   Cluster: {query.cluster} / {query.database}")

        rows, card = runner.run_golden_query(query, validate_only=args.validate)

        if args.json:
            print(json.dumps({"rows": rows, "data_card": asdict(card)}, indent=2, default=str))
        else:
            print(f"\n{_format_table(rows)}")
            print(card.render())

    # Ad-hoc KQL mode
    elif args.kql:
        if not args.cluster or not args.db:
            print("ERROR: --cluster and --db required for ad-hoc KQL", file=sys.stderr)
            sys.exit(1)

        print(f"⏳ Running ad-hoc KQL against {args.cluster}/{args.db}...")
        rows, elapsed = runner.execute(args.cluster, args.db, args.kql, timeout_sec=args.timeout)
        freshness = runner.detect_freshness(rows)

        if args.json:
            print(json.dumps({
                "rows": rows,
                "freshness": freshness,
                "elapsed_sec": elapsed,
                "row_count": len(rows),
            }, indent=2, default=str))
        else:
            print(f"\n{_format_table(rows)}")
            print(f"\n📊 Ad-hoc Query Result")
            print(f"{'─' * 45}")
            print(f"Source:     {args.cluster}/{args.db}")
            print(f"Freshness:  {freshness}")
            print(f"Rows:       {len(rows)}")
            print(f"Exec time:  {elapsed:.1f}s")
            print(f"Confidence: AD-HOC (not validated)")
            print(f"{'─' * 45}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
