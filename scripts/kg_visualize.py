"""Artha KB graph visualizer — generates a standalone HTML file using vis.js.

Usage:
    python scripts/kg_visualize.py [--output visuals/knowledge_graph.html]
    python scripts/kg_visualize.py --domain infrastructure
    python scripts/kg_visualize.py --community community-0003

Security note (pii-guard: work-data):
    Entity names are work data (project/service names, initials) — classified
    as non-PII per pii_guard.py.  Output HTML is gitignored (visuals/).
    Node labels are HTML-escaped before insertion.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

# Make scripts/ importable when run directly
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.knowledge_graph import get_kb

# ── Domain colour palette (consistent with domain_registry.yaml) ────────────
_DOMAIN_COLOURS: dict[str, str] = {
    "finance":     "#4CAF50",
    "immigration": "#2196F3",
    "health":      "#F44336",
    "kids":        "#FF9800",
    "home":        "#9C27B0",
    "employment":  "#00BCD4",
    "travel":      "#E91E63",
    "learning":    "#3F51B5",
    "vehicle":     "#607D8B",
    "insurance":   "#795548",
    "estate":      "#009688",
    "calendar":    "#FFEB3B",
    "comms":       "#FF5722",
    "social":      "#8BC34A",
    "digital":     "#03A9F4",
    "shopping":    "#FFC107",
    "wellness":    "#CDDC39",
    "boundary":    "#FF9800",
    "pets":        "#4DB6AC",
    "decisions":   "#B0BEC5",
    "caregiving":  "#CE93D8",
    "work":        "#1565C0",
    "mixed":       "#90A4AE",
}
_DEFAULT_COLOUR = "#90A4AE"


def _domain_colour(domain: str | None) -> str:
    return _DOMAIN_COLOURS.get((domain or "mixed").lower(), _DEFAULT_COLOUR)


def _html_escape(s: str) -> str:
    """HTML-escape a string for safe insertion into vis.js dataset."""
    return html.escape(str(s), quote=True)


def build_vis_data(
    kg,  # KnowledgeGraph instance
    domain: str | None = None,
    community_id: str | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """Query KB and return (nodes, edges, domain_list) for vis.js."""

    # ── Entities ────────────────────────────────────────────────────────────
    if domain:
        entity_rows = kg._conn.execute(
            "SELECT id, name, entity_type, domain, confidence, lifecycle_stage "
            "FROM entities WHERE domain=?",
            (domain,),
        ).fetchall()
    elif community_id:
        entity_rows = kg._conn.execute(
            """SELECT e.id, e.name, e.entity_type, e.domain, e.confidence, e.lifecycle_stage
               FROM entities e
               JOIN community_members cm ON e.id = cm.entity_id
               WHERE cm.community_id = ?""",
            (community_id,),
        ).fetchall()
    else:
        entity_rows = kg._conn.execute(
            "SELECT id, name, entity_type, domain, confidence, lifecycle_stage "
            "FROM entities"
        ).fetchall()

    entity_set = {row["id"] for row in entity_rows}

    # Count degrees for node sizing
    degree: dict[str, int] = {row["id"]: 0 for row in entity_rows}
    edge_rows = kg._conn.execute(
        "SELECT from_entity, to_entity, rel_type, confidence "
        "FROM relationships WHERE valid_to IS NULL"
    ).fetchall()
    for row in edge_rows:
        if row["from_entity"] in entity_set:
            degree[row["from_entity"]] = degree.get(row["from_entity"], 0) + 1
        if row["to_entity"] in entity_set:
            degree[row["to_entity"]] = degree.get(row["to_entity"], 0) + 1

    # Community membership map
    community_map: dict[str, str] = {}
    cm_rows = kg._conn.execute(
        "SELECT entity_id, community_id FROM community_members"
    ).fetchall()
    for row in cm_rows:
        if row["entity_id"] in entity_set:
            community_map[row["entity_id"]] = row["community_id"]

    domains: list[str] = sorted({row["domain"] or "mixed" for row in entity_rows})

    nodes: list[dict] = []
    for row in entity_rows:
        eid = row["id"]
        entity_domain = (row["domain"] or "mixed").lower()
        deg = degree.get(eid, 0)
        # Node size: 10 base + log-ish scaling by degree (god nodes = 40+)
        size = min(10 + deg * 3, 50)
        label = _html_escape(row["name"] or eid)
        title_parts = [
            f"<b>{label}</b>",
            f"Type: {_html_escape(row['entity_type'] or 'unknown')}",
            f"Domain: {_html_escape(entity_domain)}",
            f"Confidence: {float(row['confidence'] or 0):.2f}",
            f"Lifecycle: {_html_escape(row['lifecycle_stage'] or 'unknown')}",
            f"Degree: {deg}",
        ]
        if eid in community_map:
            title_parts.append(f"Community: {_html_escape(community_map[eid])}")
        nodes.append({
            "id": eid,
            "label": label,
            "title": "<br>".join(title_parts),
            "color": _domain_colour(entity_domain),
            "size": size,
            "group": entity_domain,
            "domain": entity_domain,
            "lifecycle": row["lifecycle_stage"] or "unknown",
            "confidence": float(row["confidence"] or 0),
        })

    # ── Edges ────────────────────────────────────────────────────────────────
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for row in edge_rows:
        fe, te = row["from_entity"], row["to_entity"]
        if fe not in entity_set or te not in entity_set:
            continue
        # Deduplicate bidirectional edges for display
        key = (min(fe, te), max(fe, te))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        conf = float(row["confidence"] or 0.5)
        edges.append({
            "from": fe,
            "to": te,
            "title": _html_escape(row["rel_type"] or ""),
            "label": _html_escape(row["rel_type"] or ""),
            "width": max(1, int(conf * 4)),
            "color": {"opacity": max(0.3, conf)},
        })

    return nodes, edges, domains


def render_html(
    nodes: list[dict],
    edges: list[dict],
    domains: list[str],
    title: str = "Artha Knowledge Graph",
) -> str:
    """Render standalone HTML with vis.js Network visualization."""
    nodes_json = json.dumps(nodes, ensure_ascii=False)
    edges_json = json.dumps(edges, ensure_ascii=False)
    domain_options = "\n".join(
        f'<option value="{_html_escape(d)}">{_html_escape(d)}</option>'
        for d in ["(all)"] + domains
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_html_escape(title)}</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1e1e2e; color: #cdd6f4; display: flex; flex-direction: column;
            height: 100vh; overflow: hidden; }}
    #toolbar {{ padding: 8px 16px; background: #181825; border-bottom: 1px solid #313244;
                display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    #toolbar h1 {{ font-size: 15px; color: #cba6f7; margin-right: 8px; }}
    #toolbar label {{ font-size: 12px; color: #a6adc8; }}
    input, select {{ background: #313244; border: 1px solid #45475a; color: #cdd6f4;
                     padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
    #stats {{ font-size: 11px; color: #6c7086; margin-left: auto; }}
    #network {{ flex: 1; }}
    #inspector {{ position: fixed; right: 16px; top: 60px; width: 260px;
                  background: #181825; border: 1px solid #313244; border-radius: 6px;
                  padding: 12px; display: none; font-size: 12px; max-height: 70vh;
                  overflow-y: auto; }}
    #inspector h2 {{ font-size: 13px; color: #89b4fa; margin-bottom: 8px; }}
    #inspector p {{ margin: 3px 0; color: #a6adc8; }}
    #inspector b {{ color: #cdd6f4; }}
  </style>
</head>
<body>
  <div id="toolbar">
    <h1>Artha KB</h1>
    <label>Search: <input id="search" type="text" placeholder="entity name…" style="width:160px"></label>
    <label>Domain: <select id="domain-filter">{domain_options}</select></label>
    <label>Lifecycle: <select id="lifecycle-filter">
      <option value="">(all)</option>
      <option>active</option><option>proposed</option><option>archived</option>
    </select></label>
    <button onclick="resetView()" style="background:#313244;border:1px solid #45475a;color:#cdd6f4;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">Reset</button>
    <span id="stats">Loading…</span>
  </div>
  <div id="network"></div>
  <div id="inspector"><h2 id="ins-title"></h2><div id="ins-body"></div></div>

  <script>
    const ALL_NODES = {nodes_json};
    const ALL_EDGES = {edges_json};

    const container = document.getElementById('network');
    const nodesDS = new vis.DataSet(ALL_NODES);
    const edgesDS = new vis.DataSet(ALL_EDGES);

    const options = {{
      nodes: {{ shape: 'dot', font: {{ color: '#cdd6f4', size: 11 }}, borderWidth: 1,
                borderWidthSelected: 3 }},
      edges: {{ arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
                font: {{ color: '#6c7086', size: 9 }}, smooth: {{ type: 'continuous' }} }},
      physics: {{ stabilization: {{ iterations: 150 }}, barnesHut: {{ gravitationalConstant: -8000 }} }},
      interaction: {{ tooltipDelay: 100, hideEdgesOnDrag: true }},
    }};

    const network = new vis.Network(container, {{ nodes: nodesDS, edges: edgesDS }}, options);

    document.getElementById('stats').textContent =
      `${{ALL_NODES.length}} entities · ${{ALL_EDGES.length}} relationships`;

    // ── Click-to-inspect ────────────────────────────────────────────────────
    const inspector = document.getElementById('inspector');
    network.on('click', params => {{
      if (params.nodes.length === 0) {{ inspector.style.display = 'none'; return; }}
      const nodeId = params.nodes[0];
      const node = ALL_NODES.find(n => n.id === nodeId);
      if (!node) return;
      document.getElementById('ins-title').innerHTML = node.label;
      document.getElementById('ins-body').innerHTML = node.title.replace(/<br>/g, '<br>');
      inspector.style.display = 'block';
    }});

    // ── Filter helpers ──────────────────────────────────────────────────────
    function applyFilters() {{
      const q = document.getElementById('search').value.toLowerCase().trim();
      const dom = document.getElementById('domain-filter').value;
      const lc = document.getElementById('lifecycle-filter').value;
      const visible = ALL_NODES.filter(n =>
        (q === '' || n.label.toLowerCase().includes(q)) &&
        (dom === '(all)' || dom === '' || n.domain === dom) &&
        (lc === '' || n.lifecycle === lc)
      ).map(n => n.id);
      const visibleSet = new Set(visible);
      nodesDS.update(ALL_NODES.map(n => ({{ id: n.id, hidden: !visibleSet.has(n.id) }})));
      edgesDS.update(ALL_EDGES.map(e => ({{
        id: e.id || e.from + '-' + e.to,
        hidden: !visibleSet.has(e.from) || !visibleSet.has(e.to)
      }})));
      document.getElementById('stats').textContent =
        `${{visible.length}} / ${{ALL_NODES.length}} entities · ${{ALL_EDGES.length}} relationships`;
    }}

    document.getElementById('search').addEventListener('input', applyFilters);
    document.getElementById('domain-filter').addEventListener('change', applyFilters);
    document.getElementById('lifecycle-filter').addEventListener('change', applyFilters);

    function resetView() {{
      document.getElementById('search').value = '';
      document.getElementById('domain-filter').value = '(all)';
      document.getElementById('lifecycle-filter').value = '';
      nodesDS.update(ALL_NODES.map(n => ({{ id: n.id, hidden: false }})));
      edgesDS.update(ALL_EDGES.map(e => ({{ id: e.id || e.from + '-' + e.to, hidden: false }})));
      document.getElementById('stats').textContent =
        `${{ALL_NODES.length}} entities · ${{ALL_EDGES.length}} relationships`;
      network.fit();
    }}
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a standalone HTML knowledge graph visualization."
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output HTML path (default: visuals/knowledge_graph.html)",
    )
    parser.add_argument("--domain", help="Filter to a single domain (e.g. infrastructure)")
    parser.add_argument("--community", help="Filter to a single community (e.g. community-0003)")
    args = parser.parse_args()

    kg = get_kb()

    t0 = __import__("time").monotonic()
    nodes, edges, domains = build_vis_data(kg, domain=args.domain, community_id=args.community)
    title = "Artha Knowledge Graph"
    if args.domain:
        title += f" — {args.domain}"
    elif args.community:
        title += f" — {args.community}"
    html_content = render_html(nodes, edges, domains, title=title)
    elapsed = __import__("time").monotonic() - t0

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        base = Path(__file__).resolve().parent.parent
        out_path = base / "visuals" / "knowledge_graph.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")

    print(
        f"Knowledge graph written to: {out_path}\n"
        f"  {len(nodes)} entities · {len(edges)} relationships · "
        f"{len(domains)} domains · rendered in {elapsed:.2f}s"
    )


if __name__ == "__main__":
    main()
