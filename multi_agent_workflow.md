# Multi-Agent Workflow

## Objective
Demonstrate a clear multi-agent AI development process where specialized agents collaborate on architecture, implementation, validation, and documentation. The runtime system itself remains a single-process/single-machine crawler and search tool.

## Agent Set
- Product Agent
- Architecture Agent
- Crawler Agent
- Search Agent
- QA Agent
- Docs Agent

Each agent has a dedicated description file in the agents directory.

## Responsibility Split
- Product Agent: converts exercise requirements into concrete acceptance criteria and non-goals.
- Architecture Agent: proposes single-machine design, backpressure strategy, and storage model.
- Crawler Agent: implements crawling pipeline, dedup, depth logic, and load controls.
- Search Agent: implements query model and live-read behavior while indexing is active.
- QA Agent: defines tests and validates correctness plus concurrency expectations.
- Docs Agent: writes evaluator-facing artifacts and keeps process traceability.

## Interaction Model
1. Product Agent defines delivery scope, required outputs, and acceptance criteria.
2. Architecture Agent proposes design and alternatives; Product Agent approves final direction.
3. Crawler Agent and Search Agent implement in parallel against shared storage contracts.
4. QA Agent runs syntax checks and integration tests, reports regressions.
5. Docs Agent compiles final documentation and recommendations.
6. Final decisions are made by the orchestrating engineer when trade-offs conflict.

## Prompt and Handoff Contracts
- Product Agent output: product_prd.md + acceptance checklist.
- Architecture Agent output: component and data-flow decisions with explicit constraints.
- Crawler Agent output: crawler.py + storage hooks for dedup/frontier/runtime heartbeat.
- Search Agent output: search.py + storage query path returning required triples.
- QA Agent output: automated tests and validation command results.
- Docs Agent output: readme.md, recommendation.md, and this workflow document.

### Prompt Templates Used
- Product Agent prompt: "Translate requirements into measurable acceptance criteria and non-goals; list explicit pass/fail checks."
- Architecture Agent prompt: "Design a single-machine system with bounded-depth crawling, dedup guarantees, backpressure, and concurrent search reads."
- Crawler Agent prompt: "Implement index(origin, k), worker pool orchestration, and queue/rate load controls with runtime telemetry."
- Search Agent prompt: "Implement query execution returning strict triples while indexing writes continue."
- QA Agent prompt: "Add tests for depth bounds, dedup invariants, live-search during indexing, and resume behavior."
- Docs Agent prompt: "Produce evaluator-ready documentation, traceability matrix, and production recommendations."

### Interaction and Review Loop
1. Product and Architecture agents agree on constraints and acceptance criteria.
2. Crawler and Search agents build in parallel using shared storage contract.
3. QA agent validates and raises defects against requirements.
4. Docs agent captures final decisions and evidence for grading.
5. Final owner resolves conflicts and merges approved outputs.

## Decision Log Highlights
- Runtime language switched to Python due missing Go toolchain in execution environment.
- SQLite WAL selected for concurrent search reads during indexing writes.
- Backpressure implemented using pending frontier limit + token-bucket RPS control.
- Resume implemented by requeueing processing tasks and continuing persisted frontier.

## Evaluation of Agent Outputs
- Functional gates:
  - index(origin, k) works with depth enforcement and dedup.
  - search(query) returns tuples with relevant_url, origin_url, depth.
  - status reports queue depth and throttled/backpressure signals.
  - search returns results while index is still running.
- Quality gates:
  - syntax compile pass
  - unit/integration tests pass
  - required documentation files exist and are consistent

## Files Produced by Workflow
- product_prd.md
- readme.md
- recommendation.md
- multi_agent_workflow.md
- grading_checklist.md
- agents/*.md
- agents/interactions_log.md
- runnable crawler/search code under src/webcrawler
