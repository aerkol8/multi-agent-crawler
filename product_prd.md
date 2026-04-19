# Product Requirements Document

## Product Name
Single-Machine Web Crawler with Live Search

## Problem Statement
Teams need a crawler that can index a large set of pages from a seed URL with controlled resource usage, while allowing operators to query newly discovered pages before the crawl is fully complete.

## Goals
- Implement index(origin, k) for bounded-depth crawling.
- Guarantee URL-level deduplication per crawl run.
- Provide explicit backpressure controls to keep load bounded.
- Implement search(query) that returns triples: (relevant_url, origin_url, depth).
- Allow search to run while indexing is active and reflect fresh indexed results.
- Provide a practical CLI for index, search, status, and run inspection.
- Support resume semantics after interruption.

## Non-Goals
- Distributed multi-machine crawling.
- JavaScript rendering.
- ML-based ranking.
- Full browser UI dashboard.

## Users
- Developers running local or server-side crawl jobs.
- Operators monitoring queue depth and crawler pressure.
- Analysts searching indexed content during active crawls.

## Core Functional Requirements

### 1) Indexing
- Input: origin URL and max depth k.
- Crawl all reachable HTTP/HTTPS links up to k hops from origin.
- Never crawl the same normalized URL twice in a run.
- Persist crawl state for observability and resume.

### 2) Backpressure
- Configurable maximum pending frontier depth.
- Configurable global requests-per-second cap.
- Fixed worker pool (bounded concurrency).
- Backpressure events are counted and exposed via status.

### 3) Search
- Input: query string.
- Output: list of tuples (relevant_url, origin_url, depth).
- Relevance: token-match with term-frequency scoring.
- Search must read from shared index while indexing writes continue.

### 4) Runtime Status and Operations
- CLI command to report run-level status, queue depth, worker activity, and throttling.
- CLI command to list recent runs.
- Resume capability for interrupted runs.
- Localhost web dashboard and JSON API for interactive operations (optional plus, implemented).

## System Design Summary
- Language/runtime: Python 3.12, stdlib-first.
- Storage: SQLite with WAL mode for concurrent reads while writing.
- Crawler: worker threads + feeder thread + token bucket limiter.
- Dedup: normalized URL + unique constraints in run_discoveries/frontier tables.
- Search index: inverted table (page_terms) updated as pages are processed.

## Data Contracts
- index(origin, k): starts or resumes crawl run.
- search(query): returns (relevant_url, origin_url, depth).
- status(run_id optional): returns run metadata, frontier counts, runtime heartbeat.

## Acceptance Criteria
- A run with k >= 1 completes and marks run status completed.
- Duplicate links are not crawled twice in the same run.
- Search returns valid tuples with origin/depth metadata.
- Search returns results before index finishes on a multi-page crawl.
- Status reflects queue depth and throttled events during crawl.
- Resume moves processing tasks back to queued and can continue.

## Milestones
1. Core schema and storage layer.
2. Crawler engine with backpressure and dedup.
3. Live search and query scoring.
4. CLI and operational status.
5. Tests and documentation.

## Risks and Mitigations
- SQLite write contention: serialize writes in process and use WAL for readers.
- Crawl explosion via broad link graph: default same-host mode and bounded depth.
- Partial failure during processing: retry fetches, mark failures, allow resume.
