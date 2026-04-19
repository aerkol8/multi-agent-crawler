from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from webcrawler.crawler import CrawlerEngine, RuntimeSnapshot
from webcrawler.search import SearchService
from webcrawler.storage import Storage
from webcrawler.utils import normalize_url


def parse_page_index(path: str) -> int | None:
    if not path.startswith("/p"):
        return None
    try:
        return int(path[2:])
    except ValueError:
        return None


def build_site_handler(total_pages: int, first_layer: int) -> type[BaseHTTPRequestHandler]:
    class SyntheticSiteHandler(BaseHTTPRequestHandler):
        pages = total_pages
        layer = max(1, min(first_layer, total_pages))

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path

            if path == "/":
                links = "".join(f"<a href='/p{i}'>seed-{i}</a>" for i in range(self.layer))
                body = f"<html><body>seed crawler profile {links}</body></html>"
                self._send_ok(body)
                return

            idx = parse_page_index(path)
            if idx is None or idx < 0 or idx >= self.pages:
                self.send_response(404)
                self.end_headers()
                return

            links = ""
            if idx < self.layer:
                second_span = max(1, self.pages - self.layer)
                nxt1 = self.layer + (idx % second_span)
                nxt2 = self.layer + ((idx + 1) % second_span)
                links = f"<a href='/p{nxt1}'>n1</a><a href='/p{nxt2}'>n2</a>"

            body = (
                "<html><body>"
                f"page {idx} crawler python indexing scalability profile data "
                f"{links}"
                "</body></html>"
            )
            self._send_ok(body)

        def _send_ok(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    return SyntheticSiteHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local scalability profile for the crawler")
    parser.add_argument("--pages", type=int, default=400, help="Total synthetic pages")
    parser.add_argument("--first-layer", type=int, default=180, help="Number of root-linked pages")
    parser.add_argument("--max-depth", type=int, default=2, help="Crawl depth k")
    parser.add_argument("--workers", type=int, default=8, help="Worker count")
    parser.add_argument("--queue-depth", type=int, default=60, help="Backpressure queue depth limit")
    parser.add_argument("--rps", type=float, default=150.0, help="Global fetch rate")
    parser.add_argument("--status-interval", type=float, default=0.2, help="Status heartbeat interval")
    parser.add_argument("--db", default="", help="Optional sqlite file path")
    parser.add_argument("--output", default="", help="Optional output JSON path")
    args = parser.parse_args()

    if args.pages < 10:
        raise SystemExit("--pages must be at least 10")

    handler = build_site_handler(total_pages=args.pages, first_layer=args.first_layer)
    site = HTTPServer(("127.0.0.1", 0), handler)
    site_thread = threading.Thread(target=site.serve_forever, daemon=True)
    site_thread.start()

    host, port = site.server_address
    origin = f"http://{host}:{port}/"
    normalized_origin = normalize_url(origin) or origin

    temp_ctx = tempfile.TemporaryDirectory() if not args.db else None
    db_path = args.db if args.db else str(Path(temp_ctx.name) / "profile.db")

    snapshots: list[RuntimeSnapshot] = []

    def collect_snapshot(snapshot: RuntimeSnapshot) -> None:
        snapshots.append(snapshot)

    storage = Storage(db_path)
    run_id = storage.create_run(normalized_origin, args.max_depth)
    storage.insert_seed(run_id, normalized_origin)

    engine = CrawlerEngine(
        storage=storage,
        run_id=run_id,
        origin_url=normalized_origin,
        max_depth=args.max_depth,
        workers=args.workers,
        pending_limit=args.queue_depth,
        requests_per_second=args.rps,
        same_host_only=True,
        status_interval_seconds=args.status_interval,
        status_hook=collect_snapshot,
    )

    run_result: list[bool] = []

    def run_engine() -> None:
        run_result.append(engine.run())

    t = threading.Thread(target=run_engine, daemon=True)

    start = time.monotonic()
    t.start()

    search_storage = Storage(db_path)
    search_service = SearchService(search_storage)
    live_search_seen = False
    max_live_hits = 0

    while t.is_alive():
        rows = search_service.search("crawler python", limit=20)
        max_live_hits = max(max_live_hits, len(rows))
        if rows:
            live_search_seen = True
        time.sleep(0.05)

    t.join(timeout=30)
    elapsed = time.monotonic() - start

    status = storage.run_status(run_id) or {}
    discovered = int(status.get("discovered_urls", 0))
    frontier = status.get("frontier", {})
    runtime = status.get("runtime") or {}

    report = {
        "run_id": run_id,
        "origin": normalized_origin,
        "pages_configured": args.pages,
        "completed": bool(run_result and run_result[0]),
        "elapsed_seconds": round(elapsed, 3),
        "discovered_urls": discovered,
        "urls_per_second": round(discovered / elapsed, 3) if elapsed > 0 else 0.0,
        "frontier": frontier,
        "runtime_last": runtime,
        "max_snapshot_queue_depth": max((s.queue_depth for s in snapshots), default=0),
        "max_snapshot_active_workers": max((s.active_workers for s in snapshots), default=0),
        "max_snapshot_throttled_events": max((s.throttled_events for s in snapshots), default=0),
        "live_search_seen_during_index": live_search_seen,
        "max_live_search_hits": max_live_hits,
    }

    print(json.dumps(report, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    search_storage.close_thread_connection()
    storage.close_thread_connection()

    site.shutdown()
    site.server_close()
    site_thread.join(timeout=2)

    if temp_ctx is not None:
        temp_ctx.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
