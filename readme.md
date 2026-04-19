# Web Crawler Multiagent

A stdlib-first Python crawler and search system that supports:
- bounded-depth indexing from a seed URL
- URL deduplication (never crawl the same page twice per run)
- queue-depth and rate-limit backpressure
- live search while indexing is still active
- status visibility and resume support

## Step-by-Step (Homework Execution)

## 1) Environment
Use Python 3.12+.

From project root:

```bash
python3 --version
```

If you use the included virtual environment in this workspace:

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" --version
```

## 2) Verify the project quickly with an end-to-end demo

This demo starts a local fixture website, starts indexing, polls search while indexing is still active, and prints final status.

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" scripts/demo_workflow.py --python "/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python"
```

Expected behavior:
- early polls show zero or few results
- later polls show more hits while indexing is still running
- final run status is completed

## 3) Start your own crawl (index)

From project root:

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db index https://example.com 2 --workers 8 --queue-depth 500 --rps 5
```

Useful options:
- --allow-external-links: follow links outside the origin host
- --status-interval 1.0: heartbeat cadence in seconds
- --quiet: suppress per-interval status printing

## 4) Search while index is running
In another terminal:

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db search "python crawler" --limit 20 --json
```

Output shape per result:
- relevant_url
- origin_url
- depth

Search semantics:
- Query terms are matched with ALL-terms logic (AND behavior).
- Result ordering uses internal ranking by summed term frequency, then shallower depth, then URL order.
- Internal ranking score is not returned in the public response contract.

## 5) Inspect status and backpressure

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db status
```

You can inspect specific runs:

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db status --run-id 1
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db runs
```

## 6) Resume after interruption

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db index --resume-run-id 1
```

Resume behavior:
- tasks left in processing state are requeued
- crawl continues from persisted frontier/discoveries
- dedup rules remain enforced

## 7) Run automated verification

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" -m compileall src tests main.py
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" -m unittest discover -s tests -v
```

## 8) Run one-command rubric evaluation

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" scripts/evaluate_submission.py --python "/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python"
```

This prints PASS/FAIL for:
- required artifacts
- compile + test health
- CLI availability
- scalability profile generation
- live search during active indexing
- strict triple output contract
- status telemetry presence
- resume behavior

## 9) Run localhost web server

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db web --host 127.0.0.1 --port 8080
```

If port 8080 is already in use, choose another port, for example:

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" main.py --db crawler.db web --host 127.0.0.1 --port 8090
```

Open in browser:
- http://127.0.0.1:8080/

Web UI capabilities:
- start indexing with origin and depth
- run search while indexing is active
- view status with queue depth and throttled events
- list runs and active state
- stop and resume runs

API endpoints (same server):
- GET /api/health
- GET /api/runs?limit=20
- GET /api/status?run_id=1
- GET /api/search?q=crawler&limit=20
- POST /api/index
- POST /api/resume
- POST /api/stop

Current automated checks validate:
- URL normalization behavior
- depth limit enforcement
- dedup invariant (same normalized URL crawled once)
- search availability while indexing is active
- single-worker backpressure progress (no deadlock)
- resume requeue semantics
- localhost web server health and startup

## Backpressure Model
- pending frontier limit: --queue-depth
- global request cap: --rps
- fixed workers: --workers
- throttled_events metric increments when pending limit blocks discovery
- deadlock-avoidance escape hatch: when all workers are busy for too long, discovery can proceed to preserve progress; treat queue depth as a soft operational limit in that edge case

## 10) Generate scalability evidence (teacher-friendly)

```bash
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" scripts/scalability_profile.py --pages 400 --workers 8 --queue-depth 60 --rps 150 --output scalability_report.json
```

This command runs a synthetic local crawl and prints JSON metrics including:
- elapsed_seconds
- discovered_urls
- urls_per_second
- max_snapshot_queue_depth
- max_snapshot_throttled_events
- live_search_seen_during_index

## CLI Commands

- index: start a new crawl or resume an existing run
- search: query indexed pages and return (relevant_url, origin_url, depth)
- status: inspect run state, queue counters, and runtime heartbeat
- runs: list recent crawl runs
- web: run localhost web dashboard and JSON API

## Requirement Traceability

See grading_checklist.md for requirement-by-requirement mapping to implementation and proof commands.

## Project Layout
- src/webcrawler/storage.py: SQLite schema and query APIs
- src/webcrawler/crawler.py: crawl engine, workers, limiter, backpressure
- src/webcrawler/search.py: query service
- src/webcrawler/cli.py: commands index/search/status/runs
- scripts/demo_workflow.py: reproducible live-index/search demo
- product_prd.md: project requirements
- multi_agent_workflow.md: multi-agent development process
- recommendation.md: production deployment recommendations

## Notes
- No external runtime dependency is required.
- SQLite WAL mode allows concurrent search reads while indexing writes are active.
- Search ranking is internal; exposed output remains strict triples.
