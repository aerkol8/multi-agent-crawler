from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .crawler import CrawlerEngine, RuntimeSnapshot
from .search import SearchService
from .storage import Storage
from .utils import normalize_url


def _status_hook(snapshot: RuntimeSnapshot) -> None:
    print(
        "[run={run}] pending={pending}/{capacity} active_workers={workers} throttled={throttled}".format(
            run=snapshot.run_id,
            pending=snapshot.pending_tasks,
            capacity=snapshot.queue_capacity,
            workers=snapshot.active_workers,
            throttled=snapshot.throttled_events,
        ),
        flush=True,
    )


def cmd_index(args: argparse.Namespace) -> int:
    storage = Storage(args.db)

    try:
        if args.resume_run_id is not None:
            run = storage.get_run(args.resume_run_id)
            if run is None:
                print(f"run id {args.resume_run_id} not found", file=sys.stderr)
                return 2
            run_id = int(run["id"])
            origin_url = str(run["origin_url"])
            max_depth = int(run["max_depth"])
            storage.requeue_processing_tasks(run_id)
            storage.set_run_status(run_id, "running")
        else:
            if args.origin is None or args.k is None:
                print("origin and k are required when not resuming", file=sys.stderr)
                return 2
            normalized_origin = normalize_url(args.origin)
            if normalized_origin is None:
                print("origin must be an http/https URL", file=sys.stderr)
                return 2
            run_id = storage.create_run(normalized_origin, args.k)
            origin_url = normalized_origin
            max_depth = args.k
            storage.insert_seed(run_id, normalized_origin)

        print(f"indexing run_id={run_id} origin={origin_url} max_depth={max_depth}")

        engine = CrawlerEngine(
            storage=storage,
            run_id=run_id,
            origin_url=origin_url,
            max_depth=max_depth,
            workers=args.workers,
            pending_limit=args.queue_depth,
            requests_per_second=args.rps,
            same_host_only=not args.allow_external_links,
            status_interval_seconds=args.status_interval,
            status_hook=None if args.quiet else _status_hook,
        )
        completed = engine.run()
        print(f"run_id={run_id} completed={completed}")
        return 0 if completed else 1
    finally:
        storage.close_thread_connection()


def cmd_search(args: argparse.Namespace) -> int:
    storage = Storage(args.db)
    try:
        service = SearchService(storage)
        results = service.search(args.query, limit=args.limit)
        if args.json:
            payload = [asdict(item) for item in results]
            print(json.dumps(payload, indent=2))
            return 0

        for item in results:
            print(f"{item.relevant_url}\t{item.origin_url}\t{item.depth}")
        return 0
    finally:
        storage.close_thread_connection()


def cmd_status(args: argparse.Namespace) -> int:
    storage = Storage(args.db)
    try:
        run_id = args.run_id
        if run_id is None:
            run_id = storage.latest_run_id()
            if run_id is None:
                print("no crawl runs found")
                return 0

        status = storage.run_status(run_id)
        if status is None:
            print(f"run id {run_id} not found", file=sys.stderr)
            return 2

        print(json.dumps(status, indent=2))
        return 0
    finally:
        storage.close_thread_connection()


def cmd_runs(args: argparse.Namespace) -> int:
    storage = Storage(args.db)
    try:
        runs = storage.list_runs(limit=args.limit)
        print(json.dumps(runs, indent=2))
        return 0
    finally:
        storage.close_thread_connection()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-machine web crawler with live search")
    parser.add_argument("--db", default="crawler.db", help="SQLite database path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Start or resume indexing")
    index_parser.add_argument("origin", nargs="?", help="Seed URL for new crawl")
    index_parser.add_argument("k", nargs="?", type=int, help="Maximum crawl depth")
    index_parser.add_argument("--resume-run-id", type=int, default=None, help="Resume an existing run id")
    index_parser.add_argument("--workers", type=int, default=8, help="Number of fetch workers")
    index_parser.add_argument("--queue-depth", type=int, default=500, help="Pending frontier depth limit")
    index_parser.add_argument("--rps", type=float, default=5.0, help="Global fetch requests per second")
    index_parser.add_argument("--allow-external-links", action="store_true", help="Follow links outside origin host")
    index_parser.add_argument("--status-interval", type=float, default=1.0, help="Seconds between status heartbeats")
    index_parser.add_argument("--quiet", action="store_true", help="Disable periodic status output")
    index_parser.set_defaults(func=cmd_index)

    search_parser = subparsers.add_parser("search", help="Search indexed pages")
    search_parser.add_argument("query", help="Query string")
    search_parser.add_argument("--limit", type=int, default=20, help="Maximum results")
    search_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    search_parser.set_defaults(func=cmd_search)

    status_parser = subparsers.add_parser("status", help="Show crawl status")
    status_parser.add_argument("--run-id", type=int, default=None, help="Specific run id")
    status_parser.set_defaults(func=cmd_status)

    runs_parser = subparsers.add_parser("runs", help="List recent runs")
    runs_parser.add_argument("--limit", type=int, default=20, help="Number of runs")
    runs_parser.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
