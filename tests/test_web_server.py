from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from webcrawler.web import create_server


class FixtureSiteHandler(BaseHTTPRequestHandler):
    routes = {
        "/": "<html><body><a href='/a'>A</a><a href='/b'>B</a>seed</body></html>",
        "/a": "<html><body>python crawler localhost ui</body></html>",
        "/b": "<html><body><a href='/c'>C</a>indexing active</body></html>",
        "/c": "<html><body>resume optional plus</body></html>",
    }

    delays = {"/b": 0.8, "/c": 0.8}

    def do_GET(self) -> None:  # noqa: N802
        body_text = self.routes.get(self.path)
        if body_text is None:
            self.send_response(404)
            self.end_headers()
            return

        delay = self.delays.get(self.path, 0.0)
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


def http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict | list]:
    body: bytes | None = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return response.status, parsed
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        parsed = json.loads(raw) if raw else {}
        return exc.code, parsed


class WebServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "web-test.db")

        self.site_server = HTTPServer(("127.0.0.1", 0), FixtureSiteHandler)
        self.site_thread = threading.Thread(target=self.site_server.serve_forever, daemon=True)
        self.site_thread.start()

        self.web_server = create_server(self.db_path, "127.0.0.1", 0)
        self.web_thread = threading.Thread(target=self.web_server.serve_forever, daemon=True)
        self.web_thread.start()

        web_host, web_port = self.web_server.server_address
        self.base_url = f"http://{web_host}:{web_port}"

        fixture_host, fixture_port = self.site_server.server_address
        self.origin = f"http://{fixture_host}:{fixture_port}/"

    def tearDown(self) -> None:
        self.web_server.shutdown()
        self.web_server.server_close()
        self.web_thread.join(timeout=2)

        self.site_server.shutdown()
        self.site_server.server_close()
        self.site_thread.join(timeout=2)

        self.tempdir.cleanup()

    def test_web_index_status_search_and_resume(self) -> None:
        status_code, health = http_json("GET", f"{self.base_url}/api/health")
        self.assertEqual(status_code, 200)
        self.assertEqual(health.get("ok"), True)

        status_code, start = http_json(
            "POST",
            f"{self.base_url}/api/index",
            {
                "origin": self.origin,
                "k": 2,
                "workers": 2,
                "queue_depth": 20,
                "rps": 30,
            },
        )
        self.assertEqual(status_code, 201)
        run_id = int(start["run_id"])

        deadline = time.time() + 20
        completed = False
        while time.time() < deadline:
            query = urlencode({"run_id": run_id})
            status_code, payload = http_json("GET", f"{self.base_url}/api/status?{query}")
            self.assertEqual(status_code, 200)
            run = payload.get("run") or {}
            if run.get("status") == "completed":
                completed = True
                break
            time.sleep(0.2)

        self.assertTrue(completed)

        with urlopen(f"{self.base_url}/api/events?run_id={run_id}", timeout=5) as stream_response:
            first_line = stream_response.readline().decode("utf-8", errors="ignore").strip()
            self.assertEqual(first_line, "event: status")

        query = urlencode({"q": "python crawler", "limit": 20, "sortBy": "relevance"})
        status_code, rows = http_json("GET", f"{self.base_url}/api/search?{query}")
        self.assertEqual(status_code, 200)
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)

        keys = set(rows[0].keys())
        self.assertEqual(keys, {"word", "url", "origin", "depth", "freq", "score"})

        status_code, runs = http_json("GET", f"{self.base_url}/api/runs?limit=5")
        self.assertEqual(status_code, 200)
        self.assertTrue(any(int(row["id"]) == run_id for row in runs))

        status_code, resumed = http_json(
            "POST",
            f"{self.base_url}/api/resume",
            {"run_id": run_id, "workers": 2, "queue_depth": 20, "rps": 30},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(int(resumed["run_id"]), run_id)


if __name__ == "__main__":
    unittest.main()
