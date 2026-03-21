# Procedure: OneNote Parallel Page Fetch

**Trigger**: Fetching OneNote pages when the API is slow or timing out
**Domain**: digital
**Discovered**: 2026-03-16 (catch-up bootstrap)

## Working Approach

1. Call `GET /me/onenote/notebooks` to enumerate notebooks (≤3 retries).
2. For each notebook, fetch sections via
   `GET /me/onenote/notebooks/{id}/sections` in parallel (asyncio or
   ThreadPoolExecutor with max_workers=4 to stay within Graph throttle).
3. Fetch pages per section with `?top=50&$select=title,lastModifiedDateTime`
   — do NOT fetch page content in bulk; content fetch is a separate call.
4. Filter pages modified in the last `scan_window_days` (default 2).
5. For modified pages, fetch full content via
   `GET /me/onenote/pages/{id}/content` with a 10s timeout per page.
6. If any section fetch times out, log warning and continue — do not abort
   the whole notebook scan.

## Pitfalls

- The `charmap` error (seen 2026-03-19) occurs when page content contains
  characters outside the codec.  Always use `encoding="utf-8", errors="replace"`
  when writing fetched content to disk or processing text.
- Graph API returns 429 (throttle) after ~4 concurrent section fetches.
  Keep max_workers ≤ 4.
- Section fetch for large notebooks (>100 sections) can exceed the 30s
  default timeout.  Set a per-request timeout of 20s and retry once.

## Verification

After fetch: sum of fetched page titles should be > 0.
If 0, check: Graph token valid, `onenote.ReadWrite` scope granted,
`features.onenote` enabled in `config/artha_config.yaml`.
