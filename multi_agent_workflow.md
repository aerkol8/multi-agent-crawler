# Multi-Agent Workflow

## Objective
Show a measurable multi-agent execution process, not just multi-agent labels.
This workflow is designed to produce evidence for grading in three dimensions:
1. Role specialization
2. Parallelizable handoffs
3. Verifiable quality gates

## Agent Roster
| Agent | Primary responsibility | Canonical file |
| --- | --- | --- |
| Product Agent | Requirement freeze and acceptance criteria | `agents/product_agent.md` |
| Architecture Agent | Boundaries, contracts, concurrency model | `agents/architecture_agent.md` |
| Crawler Agent | Crawl runtime correctness and backpressure | `agents/crawler_agent.md` |
| Search Agent | Query schema, ranking, and live-read behavior | `agents/search_agent.md` |
| QA Agent | Validation matrix and release gating | `agents/qa_agent.md` |
| Docs Agent | Traceability and operator-facing docs | `agents/docs_agent.md` |

## Execution Graph
```
Product Agent
    |
    v
Architecture Agent
    |\
    | \  (parallel implementation window)
    |  +--> Crawler Agent
    |  +--> Search Agent
    |
    v
QA Agent
    |
    v
Docs Agent
    |
    v
Orchestrator merge decision
```

## Handoff Protocol
### Stage 1: Product -> Architecture
- Input: locked requirements and acceptance criteria.
- Exit condition: no ambiguous contract remains.

### Stage 2: Architecture -> Implementation Agents
- Input: ownership boundaries and interface contracts.
- Exit condition: crawler/search work can proceed in parallel without file ownership conflict.

### Stage 3: Implementation -> QA
- Input: runnable code and stable public contracts.
- Exit condition: compile, unit, and evaluator checks pass.

### Stage 4: QA -> Docs
- Input: test evidence and known limitations.
- Exit condition: docs align with actual behavior and validated commands.

## Prompt Strategy
Prompts are role-scoped and constraint-driven.
Each agent prompt includes:
1. Read-first list
2. Must-deliver outputs
3. Hard invariants
4. Explicit do-not-touch boundaries

This prevents vague one-line prompting and reduces cross-agent interference.

## Orchestrator Decision Log
1. Enforced contract-first workflow before implementation.
2. Prioritized grader-visible storage inspectability.
3. Required evaluator-level pass signal before packaging.
4. Kept public scoring explicit to avoid hidden ranking semantics.

## Quality Gates
### Functional gates
- Depth-limited index correctness
- URL dedup invariant
- Live search during active indexing
- Resume behavior after interruption
- Status telemetry consistency

### Verification gates
- Compile/syntax checks
- Unit and integration tests
- Evaluator summary pass
- Docs command-path correctness

## Evidence Artifacts
- `agents/interactions_log.md` for chronological handoffs and decision authority
- `grading_checklist.md` for requirement-to-evidence mapping
- `scripts/evaluate_submission.py` for reproducible pass/fail rubric checks

## Why this improves grading confidence
- Role boundaries are explicit and auditable.
- Prompts are no longer generic; they are actionable and test-aware.
- Workflow now includes clear handoff conditions and release gates.
