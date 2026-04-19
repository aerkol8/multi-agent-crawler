# Multi-Agent Interaction Log

This log records how each agent contributed and how outputs were reconciled.

## Iteration 1 - Scope and constraints
- Product Agent: formalized acceptance criteria and non-goals from homework prompt.
- Architecture Agent: proposed single-machine, WAL-backed storage and bounded-concurrency crawler design.
- Decision: use stdlib-first implementation with SQLite as the only persistence dependency.

## Iteration 2 - Core implementation split
- Crawler Agent: implemented frontier processing, dedup flow, depth handling, backpressure controls.
- Search Agent: implemented token indexing and query path returning triples.
- Interface contract: crawler writes page_terms and run_discoveries, search reads both for result assembly.

## Iteration 3 - Concurrency and reliability hardening
- QA Agent: added live-search-during-indexing test and resume-state test.
- QA Agent: identified single-worker backpressure progress risk; requested non-deadlocking fallback.
- Crawler Agent: added deadlock-avoidance escape hatch while preserving throttle counters.

## Iteration 4 - Documentation and evaluator evidence
- Docs Agent: produced README step-by-step runbook.
- Docs Agent: produced grading_checklist.md to map requirements to files/tests.
- Docs Agent: refined workflow docs to include explicit prompts and handoff contracts.

## Final Decision Authority
When outputs conflicted (strict contract output vs internal ranking metadata), final integration removed public score from search output while keeping internal ranking for ordering only.
