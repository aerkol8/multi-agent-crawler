# Agent 04 - Search Agent

## Mission
Implement query execution over indexed crawl data with deterministic ranking and live-read behavior during indexing.

## Read First (strict order)
1. `product_prd.md` search contract section.
2. Architecture search/storage contracts in `multi_agent_workflow.md`.
3. Data model definitions in `src/webcrawler/models.py`.
4. Storage search path in `src/webcrawler/storage.py`.

## Responsibilities
- Implement tokenized query execution over persisted terms.
- Enforce public output schema and deterministic ranking.
- Support sort modes required by API/CLI contracts.
- Keep search available while indexing is active.
- Preserve stability under partial or sparse data.

## Locked Contract
- Public fields: `word`, `url`, `origin`, `depth`, `freq`, `score`.
- Public score formula: `(freq * 10) + 1000 - (depth * 5)`.
- Sort support: `relevance` and `depth` (as documented contract).

## Prompt Packet (orchestrator template)
"You are Agent 04 (Search). Implement search(query) over current indexed state.
Keep schema and score formula exactly aligned with the public contract.
Support documented sort modes and ensure behavior remains stable while crawl writes continue."

## Do Not Touch
- Crawl worker orchestration logic.
- Web UI rendering logic unless API contract changes are explicitly approved.

## Required Evidence Before Merge
- Search results match expected schema keys exactly.
- Score formula passes numeric check against sample rows.
- Live-search test returns hits before indexing fully completes.

## Done Checklist
- Search service and storage query path are contract-compliant.
- Sorting behavior is deterministic.
- No regression in live indexing + search coexistence.
