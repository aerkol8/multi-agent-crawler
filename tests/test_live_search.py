from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from webcrawler.crawler import CrawlerEngine
from webcrawler.search import SearchService
from webcrawler.storage import Storage
from webcrawler.utils import normalize_url


class FixtureHandler(BaseHTTPRequestHandler):
    routes = {
        "/": "<html><body><a href='/a'>A</a><a href='/b'>B</a>seed page</body></html>",
        "/a": "<html><head><title>A</title></head><body>python crawler indexing example</body></html>",
        "/b": "<html><body><a href='/c'>C</a>slow page</body></html>",
        "/c": "<html><body>search freshness crawler update stream</body></html>",
    }

    delays = {"/b": 1.0, "/c": 0.6}

    def do_GET(self) -> None:  # noqa: N802
        payload = self.routes.get(self.path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return

        delay = self.delays.get(self.path)
        if delay:
            time.sleep(delay)

        body = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def start_fixture_server() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/"


class LiveSearchTestCase(unittest.TestCase):
    def test_search_returns_results_while_indexing_active(self) -> None:
        server, server_thread, origin = start_fixture_server()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = str(Path(tmp) / "crawler.db")
                storage = Storage(db_path)

                run_id = storage.create_run(normalize_url(origin) or origin, 2)
                storage.insert_seed(run_id, normalize_url(origin) or origin)

                engine = CrawlerEngine(
                    storage=storage,
                    run_id=run_id,
                    origin_url=normalize_url(origin) or origin,
                    max_depth=2,
                    workers=2,
                    pending_limit=100,
                    requests_per_second=20,
                    same_host_only=True,
                    status_interval_seconds=0.2,
                )
                worker_thread = threading.Thread(target=engine.run, daemon=True)
                worker_thread.start()

                search = SearchService(storage)
                deadline = time.time() + 10
                found_while_running = False

                while time.time() < deadline:
                    results = search.search("python crawler", limit=10)
                    if results and worker_thread.is_alive():
                        first = results[0]
                        self.assertTrue(hasattr(first, "word"))
                        self.assertTrue(hasattr(first, "url"))
                        self.assertTrue(hasattr(first, "origin"))
                        self.assertTrue(hasattr(first, "depth"))
                        self.assertTrue(hasattr(first, "freq"))
                        self.assertTrue(hasattr(first, "score"))
                        found_while_running = True
                        break
                    if not worker_thread.is_alive():
                        break
                    time.sleep(0.1)

                worker_thread.join(timeout=10)

                status = storage.run_status(run_id)
                self.assertIsNotNone(status)
                assert status is not None
                self.assertEqual(status["run"]["status"], "completed")
                self.assertTrue(found_while_running, json.dumps(status, indent=2))
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    def test_resume_requeues_processing_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "crawler.db")
            storage = Storage(db_path)
            origin = "http://example.com/"
            run_id = storage.create_run(origin, 1)
            storage.insert_seed(run_id, origin)

            claimed = storage.claim_queued_tasks(run_id, 1)
            self.assertTrue(claimed)

            before = storage.frontier_counts(run_id)
            self.assertEqual(before["processing"], 1)

            storage.requeue_processing_tasks(run_id)
            after = storage.frontier_counts(run_id)
            self.assertEqual(after["processing"], 0)
            self.assertEqual(after["queued"], 1)


if __name__ == "__main__":
    unittest.main()
