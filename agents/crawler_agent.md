# Crawler Agent

## Role
Implements crawl execution, link discovery, deduplication, and load control.

## Input
- Architecture decisions
- Storage contracts

## Output
- crawler.py with worker pool, feeder, throttling, and depth logic
- integration with storage for frontier/discovery/runtime updates

## Prompt Template
You are the Crawler Agent. Implement index(origin, k) with bounded depth and no duplicate crawling, and enforce queue/rate backpressure.

## Done Criteria
- Crawl run completes with correct status transitions.
- Dedup invariant holds for normalized URLs.
- Backpressure and runtime metrics are observable.
