from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from webcrawler.crawler import CrawlerEngine
from webcrawler.storage import Storage
from webcrawler.utils import normalize_url


def start_graph_server(routes: dict[str, str], delays: dict[str, float] | None = None) -> tuple[HTTPServer, threading.Thread, type[BaseHTTPRequestHandler]]:
    delays = delays or {}

    class GraphHandler(BaseHTTPRequestHandler):
        route_map = routes
        delay_map = delays
        hits: dict[str, int] = {}
        hits_lock = threading.Lock()

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            with self.hits_lock:
                self.hits[path] = self.hits.get(path, 0) + 1

            body_text = self.route_map.get(path)
            if body_text is None:
                self.send_response(404)
                self.end_headers()
                return

            delay = self.delay_map.get(path, 0.0)
            if delay > 0:
                time.sleep(delay)

            body = body_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = HTTPServer(("127.0.0.1", 0), GraphHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, GraphHandler


class CrawlInvariantTestCase(unittest.TestCase):
    def test_depth_limit_and_dedup(self) -> None:
        routes = {
            "/": "<a href='/dup'>dup1</a><a href='/x/../dup'>dup2</a><a href='/level1'>l1</a>",
            "/dup": "duplicate target",
            "/level1": "<a href='/level2'>l2</a>depth one",
            "/level2": "depth two",
        }
        server, server_thread, handler = start_graph_server(routes)

        try:
            host, port = server.server_address
            origin = f"http://{host}:{port}/"
            normalized_origin = normalize_url(origin) or origin

            with tempfile.TemporaryDirectory() as tmp:
                db_path = str(Path(tmp) / "crawler.db")
                storage = Storage(db_path)
                run_id = storage.create_run(normalized_origin, 1)
                storage.insert_seed(run_id, normalized_origin)

                engine = CrawlerEngine(
                    storage=storage,
                    run_id=run_id,
                    origin_url=normalized_origin,
                    max_depth=1,
                    workers=2,
                    pending_limit=50,
                    requests_per_second=50,
                    same_host_only=True,
                    status_interval_seconds=0.1,
                )
                completed = engine.run()
                self.assertTrue(completed)

                status = storage.run_status(run_id)
                self.assertIsNotNone(status)
                assert status is not None
                self.assertEqual(status["run"]["status"], "completed")

                # origin + dup + level1 only. level2 is depth=2 and should not be discovered.
                self.assertEqual(status["discovered_urls"], 3)

                self.assertEqual(handler.hits.get("/dup", 0), 1)
                self.assertEqual(handler.hits.get("/level2", 0), 0)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    def test_single_worker_backpressure_makes_progress(self) -> None:
        links = "".join(f"<a href='/p{i}'>p{i}</a>" for i in range(12))
        routes = {"/": links}
        routes.update({f"/p{i}": f"page {i}" for i in range(12)})
        delays = {f"/p{i}": 0.05 for i in range(12)}

        server, server_thread, _handler = start_graph_server(routes, delays=delays)
        try:
            host, port = server.server_address
            origin = f"http://{host}:{port}/"
            normalized_origin = normalize_url(origin) or origin

            with tempfile.TemporaryDirectory() as tmp:
                db_path = str(Path(tmp) / "crawler.db")
                storage = Storage(db_path)
                run_id = storage.create_run(normalized_origin, 1)
                storage.insert_seed(run_id, normalized_origin)

                engine = CrawlerEngine(
                    storage=storage,
                    run_id=run_id,
                    origin_url=normalized_origin,
                    max_depth=1,
                    workers=1,
                    pending_limit=2,
                    requests_per_second=50,
                    same_host_only=True,
                    status_interval_seconds=0.1,
                )

                result: list[bool] = []

                def run_engine() -> None:
                    result.append(engine.run())

                t = threading.Thread(target=run_engine, daemon=True)
                t.start()
                t.join(timeout=20)
                if t.is_alive():
                    engine.stop()
                    t.join(timeout=3)
                    self.fail("crawler did not complete under single-worker backpressure")

                self.assertTrue(result and result[0])

                status = storage.run_status(run_id)
                self.assertIsNotNone(status)
                assert status is not None
                self.assertEqual(status["run"]["status"], "completed")
                runtime = status.get("runtime") or {}
                self.assertGreater(runtime.get("throttled_events", 0), 0)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
