# Grading Checklist (Requirement Traceability)

Use this file as a quick evaluator map from requirements to implementation and evidence.

Fast path:
"/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" scripts/evaluate_submission.py --python "/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python"

| Requirement | Implementation | Verification |
|---|---|---|
| index(origin, k) crawls up to depth k | src/webcrawler/crawler.py + src/webcrawler/storage.py | python -m unittest discover -s tests -v (test_depth_limit_and_dedup) |
| Never crawl same page twice | URL normalization in src/webcrawler/utils.py + UNIQUE(run_id, url) in run_discoveries/frontier | python -m unittest discover -s tests -v (test_depth_limit_and_dedup) |
| Backpressure controls | pending_limit + token bucket limiter in src/webcrawler/crawler.py | python -m unittest discover -s tests -v (test_single_worker_backpressure_makes_progress) |
| search(query) returns (relevant_url, origin_url, depth) | src/webcrawler/models.py + src/webcrawler/storage.py + src/webcrawler/search.py | python main.py --db demo.db search "crawler" --json |
| Search while indexing is active | SQLite WAL + concurrent process-safe reads/writes in src/webcrawler/storage.py | python -m unittest discover -s tests -v (test_search_returns_results_while_indexing_active) |
| Search contract semantics documented | readme.md + product_prd.md (ALL-terms matching and tuple-only output) | review docs and run python main.py --db demo.db search "crawler python" --json |
| Simple CLI for index/search/status | src/webcrawler/cli.py | python main.py --help |
| View indexing progress/queue/backpressure status | runtime_state recording and status command in src/webcrawler/storage.py and src/webcrawler/cli.py | python main.py --db demo.db status |
| Resume after interruption | requeue_processing_tasks + --resume-run-id in src/webcrawler/storage.py and src/webcrawler/cli.py | python -m unittest discover -s tests -v (test_resume_requeues_processing_tasks) |
| Optional plus: localhost web server UI/API | src/webcrawler/web.py + main.py web command | python scripts/evaluate_submission.py --python <python-bin> (Localhost web server PASS) |
| Scalability evidence on localhost | scripts/scalability_profile.py + operational thresholds in recommendation.md | python scripts/scalability_profile.py --pages 400 --workers 8 --queue-depth 60 --rps 150 --output scalability_report.json |
| Multi-agent workflow proof | multi_agent_workflow.md + agents/*.md + agents/interactions_log.md | review docs directly |

## Reproducible End-to-End Proof

1. Run the demo:
   "/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python" scripts/demo_workflow.py --python "/Users/aerkol/Desktop/web crawler multiagent/.venv/bin/python"
2. Confirm live polling shows increasing hits before indexing completes.
3. Confirm final status is completed.
4. Confirm search output contains only triple fields.
