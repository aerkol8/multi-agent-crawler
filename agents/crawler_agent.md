# Agent 03 - Crawler Agent

## Mission
Implement crawl execution, frontier progression, fetch/parse integration, dedup, and load control.
This agent owns runtime crawl correctness.

## Read First (strict order)
1. `product_prd.md`
2. Architecture contracts from `multi_agent_workflow.md`
3. Storage interfaces in `src/webcrawler/storage.py`
4. URL/token utilities in `src/webcrawler/utils.py`

## Responsibilities
- Implement bounded-depth crawl progression.
- Guarantee no duplicate crawl for the same normalized URL in a run.
- Integrate queue depth and request rate backpressure controls.
- Emit/record runtime telemetry required by status endpoints.
- Preserve resumability via safe state transitions.

## Owned Runtime Surface
- `src/webcrawler/crawler.py`
- crawler-facing storage integration points (frontier/discovery/runtime updates)

## Hard Invariants
- Never process URLs with depth greater than `k`.
- Dedup check must happen before enqueue and before processing.
- Backpressure must slow intake but must not deadlock progress.
- Failures should mark task state deterministically (`failed` vs retriable path).
- Completion must be based on queue + active worker quiescence.

## Prompt Packet (orchestrator template)
"You are Agent 03 (Crawler). Implement index execution with worker threads, feeder flow, and bounded depth.
Honor dedup invariants and queue/rate backpressure. Ensure runtime status fields remain consistent.
Do not change search output contracts. Keep stop/resume semantics safe and observable."

## Do Not Touch
- Public search schema definitions.
- Documentation-only files unless orchestrator explicitly asks.
- Evaluator logic except when a crawler contract change requires it.

## Required Evidence Before Merge
- Depth-bound and dedup tests pass.
- Single-worker backpressure still makes progress.
- Resume path requeues processing tasks correctly.

## Done Checklist
- Crawl statuses transition correctly under success/failure/stop.
- Backpressure counters and queue metrics are visible in status.
- Implementation passes crawler-focused tests without flaky timing.
