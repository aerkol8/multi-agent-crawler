from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .crawler import CrawlerEngine
from .search import SearchService
from .storage import Storage
from .utils import normalize_url


@dataclass
class CrawlJob:
    run_id: int
    engine: CrawlerEngine
    thread: threading.Thread | None
    started_at: float


class WebAppState:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._jobs: dict[int, CrawlJob] = {}
        self._jobs_lock = threading.RLock()

    def _with_storage(self) -> Storage:
        return Storage(self.db_path)

    def _cleanup_finished_jobs_locked(self) -> None:
        finished = [
            run_id
            for run_id, job in self._jobs.items()
            if job.thread is None or not job.thread.is_alive()
        ]
        for run_id in finished:
            self._jobs.pop(run_id, None)

    def _run_job(self, job: CrawlJob, storage: Storage) -> None:
        try:
            job.engine.run()
        finally:
            storage.close_thread_connection()
            with self._jobs_lock:
                current = self._jobs.get(job.run_id)
                if current is job:
                    self._jobs.pop(job.run_id, None)

    def _start_engine(
        self,
        run_id: int,
        origin_url: str,
        max_depth: int,
        workers: int,
        queue_depth: int,
        requests_per_second: float,
        allow_external_links: bool,
        status_interval: float,
    ) -> None:
        with self._jobs_lock:
            self._cleanup_finished_jobs_locked()
            existing = self._jobs.get(run_id)
            if existing and existing.thread.is_alive():
                raise ValueError(f"run id {run_id} is already active")

            storage = self._with_storage()
            engine = CrawlerEngine(
                storage=storage,
                run_id=run_id,
                origin_url=origin_url,
                max_depth=max_depth,
                workers=max(1, workers),
                pending_limit=max(1, queue_depth),
                requests_per_second=max(0.0, requests_per_second),
                same_host_only=not allow_external_links,
                status_interval_seconds=max(0.2, status_interval),
                status_hook=None,
            )
            job = CrawlJob(run_id=run_id, engine=engine, thread=None, started_at=time.time())
            thread = threading.Thread(
                target=self._run_job,
                args=(job, storage),
                daemon=True,
                name=f"web-crawl-{run_id}",
            )
            job.thread = thread
            self._jobs[run_id] = job
            thread.start()

    def start_new_run(
        self,
        origin: str,
        max_depth: int,
        workers: int,
        queue_depth: int,
        requests_per_second: float,
        allow_external_links: bool,
        status_interval: float,
    ) -> dict[str, Any]:
        normalized = normalize_url(origin)
        if normalized is None:
            raise ValueError("origin must be an http/https URL")

        storage = self._with_storage()
        try:
            run_id = storage.create_run(normalized, max_depth)
            storage.insert_seed(run_id, normalized)
        finally:
            storage.close_thread_connection()

        self._start_engine(
            run_id=run_id,
            origin_url=normalized,
            max_depth=max_depth,
            workers=workers,
            queue_depth=queue_depth,
            requests_per_second=requests_per_second,
            allow_external_links=allow_external_links,
            status_interval=status_interval,
        )

        return {"run_id": run_id, "origin_url": normalized, "max_depth": max_depth}

    def resume_run(
        self,
        run_id: int | None,
        workers: int,
        queue_depth: int,
        requests_per_second: float,
        allow_external_links: bool,
        status_interval: float,
    ) -> dict[str, Any]:
        storage = self._with_storage()
        try:
            target_run_id = run_id if run_id is not None else storage.latest_run_id()
            if target_run_id is None:
                raise ValueError("no crawl run found to resume")

            run = storage.get_run(target_run_id)
            if run is None:
                raise ValueError(f"run id {target_run_id} not found")

            storage.requeue_processing_tasks(target_run_id)
            storage.set_run_status(target_run_id, "running")

            origin_url = str(run["origin_url"])
            max_depth = int(run["max_depth"])
        finally:
            storage.close_thread_connection()

        self._start_engine(
            run_id=target_run_id,
            origin_url=origin_url,
            max_depth=max_depth,
            workers=workers,
            queue_depth=queue_depth,
            requests_per_second=requests_per_second,
            allow_external_links=allow_external_links,
            status_interval=status_interval,
        )

        return {"run_id": target_run_id, "origin_url": origin_url, "max_depth": max_depth}

    def stop_run(self, run_id: int) -> bool:
        with self._jobs_lock:
            self._cleanup_finished_jobs_locked()
            job = self._jobs.get(run_id)
            if not job or job.thread is None or not job.thread.is_alive():
                return False
            job.engine.stop()
            return True

    def is_active(self, run_id: int) -> bool:
        with self._jobs_lock:
            self._cleanup_finished_jobs_locked()
            job = self._jobs.get(run_id)
            return bool(job and job.thread is not None and job.thread.is_alive())

    def list_runs(self, limit: int) -> list[dict[str, Any]]:
        storage = self._with_storage()
        try:
            runs = storage.list_runs(limit=limit)
        finally:
            storage.close_thread_connection()

        for row in runs:
            row["active"] = self.is_active(int(row["id"]))
        return runs

    def run_status(self, run_id: int | None) -> dict[str, Any] | None:
        storage = self._with_storage()
        try:
            target = run_id if run_id is not None else storage.latest_run_id()
            if target is None:
                return None
            payload = storage.run_status(target)
        finally:
            storage.close_thread_connection()

        if payload is None:
            return None
        payload["active"] = self.is_active(target)
        return payload

    def search(self, query: str, limit: int, sort_by: str) -> list[dict[str, Any]]:
      storage = self._with_storage()
      try:
        rows = SearchService(storage).search(query=query, limit=limit, sort_by=sort_by)
        return [asdict(row) for row in rows]
      finally:
        storage.close_thread_connection()

DASHBOARD_HTML = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Crawler Dashboard</title>
    <style>
      :root {
        --bg: #f5f7fb;
        --panel: #ffffff;
        --ink: #1d2433;
        --muted: #667089;
        --accent: #006d77;
        --border: #d8deea;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        padding: 24px;
        background: radial-gradient(circle at 10% 10%, #edf3ff 0, #f5f7fb 55%);
        color: var(--ink);
        font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      }
      h1 { margin: 0 0 14px 0; }
      .layout {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 16px;
      }
      .panel {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 16px;
        box-shadow: 0 6px 18px rgba(22, 40, 70, 0.08);
      }
      .title {
        margin: 0 0 10px 0;
        font-size: 1.05rem;
      }
      form {
        display: grid;
        gap: 8px;
      }
      input, button, select {
        width: 100%;
        min-width: 0;
        font-size: 0.95rem;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid var(--border);
      }
      button {
        border: none;
        color: #fff;
        background: linear-gradient(120deg, var(--accent), #2a9d8f);
        cursor: pointer;
      }
      button.secondary {
        background: #4f5d75;
      }
      .row {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
      }
      .muted { color: var(--muted); font-size: 0.9rem; }
      pre {
        margin: 0;
        max-height: 260px;
        overflow: auto;
        white-space: pre-wrap;
        word-break: break-word;
        background: #0f1724;
        color: #def0ff;
        border-radius: 10px;
        padding: 10px;
        font-size: 0.85rem;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9rem;
      }
      th, td {
        border-bottom: 1px solid var(--border);
        text-align: left;
        padding: 6px 4px;
        vertical-align: top;
        word-break: break-word;
        overflow-wrap: anywhere;
      }
      .chip {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        background: #edf7f8;
        color: #004f57;
        font-size: 0.8rem;
      }
      @media (max-width: 860px) {
        body { padding: 14px; }
        .layout { grid-template-columns: 1fr; }
        .row { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <h1>Localhost Crawler Dashboard</h1>
    <p class=\"muted\">Start index, search during active crawl, inspect status, stop or resume runs.</p>

    <div class=\"layout\">
      <section class=\"panel\">
        <h2 class=\"title\">Start Index</h2>
        <form id=\"start-form\">
          <input id=\"origin\" required placeholder=\"Origin URL (https://example.com)\" />
          <div class=\"row\">
            <input id=\"depth\" type=\"number\" min=\"0\" value=\"2\" placeholder=\"Depth k\" />
            <input id=\"workers\" type=\"number\" min=\"1\" value=\"8\" placeholder=\"Workers\" />
          </div>
          <div class=\"row\">
            <input id=\"queue\" type=\"number\" min=\"1\" value=\"500\" placeholder=\"Queue depth\" />
            <input id=\"rps\" type=\"number\" min=\"0\" step=\"0.1\" value=\"5\" placeholder=\"RPS\" />
          </div>
          <button type=\"submit\">Start</button>
        </form>
      </section>

      <section class=\"panel\">
        <h2 class=\"title\">Resume / Stop</h2>
        <form id=\"resume-form\">
          <input id=\"resume-run-id\" type=\"number\" min=\"1\" placeholder=\"Run ID (blank = latest)\" />
          <button type=\"submit\" class=\"secondary\">Resume</button>
        </form>
        <form id=\"stop-form\" style=\"margin-top:10px\">
          <input id=\"stop-run-id\" type=\"number\" min=\"1\" required placeholder=\"Run ID to stop\" />
          <button type=\"submit\" class=\"secondary\">Stop</button>
        </form>
        <p id=\"stop-feedback\" class=\"muted\" style=\"margin-top:8px; min-height:1.2em;\"></p>
      </section>

      <section class=\"panel\">
        <h2 class=\"title\">Search</h2>
        <form id=\"search-form\">
          <input id=\"query\" required placeholder=\"Search query\" />
          <div class=\"row\">
            <input id=\"limit\" type=\"number\" min=\"1\" value=\"20\" placeholder=\"Limit\" />
            <button type=\"submit\">Search</button>
          </div>
          <select id=\"sort-by\">
            <option value=\"relevance\" selected>Sort: relevance</option>
            <option value=\"depth\">Sort: depth</option>
          </select>
        </form>
        <pre id=\"search-results\">[]</pre>
        <p id=\"delete-feedback\" class=\"muted\" style=\"margin-top:8px; min-height:1.2em;\"></p>
      </section>

      <section class=\"panel\">
        <h2 class=\"title\">Run Status</h2>
        <p class=\"muted\">Live status stream uses SSE; polling fallback is automatic if needed.</p>
        <pre id=\"status-results\">{}</pre>
      </section>

      <section class=\"panel\" style=\"grid-column: 1 / -1\">
        <h2 class=\"title\">Recent Runs</h2>
        <table id=\"runs-table\">
          <thead>
            <tr><th>ID</th><th>Origin</th><th>Depth</th><th>Status</th><th>Active</th></tr>
          </thead>
          <tbody></tbody>
        </table>
      </section>
    </div>

    <script>
      let statusStream = null;
      let streamedRunId = null;
      let runsRefreshInFlight = false;
      let runsRefreshPending = false;

      async function api(method, path, payload) {
        const options = { method, headers: { 'Content-Type': 'application/json' } };
        if (payload) { options.body = JSON.stringify(payload); }
        const res = await fetch(path, options);
        const json = await res.json();
        if (!res.ok) {
          throw new Error(json.error || ('HTTP ' + res.status));
        }
        return json;
      }

      function setPre(id, data) {
        document.getElementById(id).textContent = JSON.stringify(data, null, 2);
      }

      async function refreshRuns() {
        if (runsRefreshInFlight) {
          runsRefreshPending = true;
          return;
        }
        runsRefreshInFlight = true;
        try {
          const runs = await api('GET', '/api/runs?limit=20');
          const tbody = document.querySelector('#runs-table tbody');
          tbody.innerHTML = '';
          for (const run of runs) {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td>' + run.id + '</td>' +
              '<td>' + run.origin_url + '</td>' +
              '<td>' + run.max_depth + '</td>' +
              '<td><span class="chip">' + run.status + '</span></td>' +
              '<td>' + (run.active ? 'yes' : 'no') + '</td>';
            tbody.appendChild(tr);
          }
        } finally {
          runsRefreshInFlight = false;
          if (runsRefreshPending) {
            runsRefreshPending = false;
            refreshRuns().catch(() => {});
          }
        }
      }

      async function refreshStatus() {
        const status = await api('GET', '/api/status');
        setPre('status-results', status || {});
        const run = status && status.run ? status.run : null;
        const isActive = Boolean(status && status.active);
        if (run && isActive) {
          openStatusStream(Number(run.id));
        }
      }

      function closeStatusStream() {
        if (statusStream) {
          statusStream.close();
          statusStream = null;
          streamedRunId = null;
        }
      }

      function openStatusStream(runId) {
        if (!window.EventSource) {
          return;
        }
        if (statusStream && streamedRunId === runId) {
          return;
        }

        closeStatusStream();
        streamedRunId = runId;
        statusStream = new EventSource('/api/events?run_id=' + runId);

        const handlePayload = (payload) => {
          setPre('status-results', payload || {});
        };

        statusStream.addEventListener('status', (event) => {
          try {
            handlePayload(JSON.parse(event.data));
          } catch (_err) {
            // ignore malformed chunks
          }
        });

        statusStream.addEventListener('done', (_event) => {
          closeStatusStream();
          refreshAll().catch(() => {});
        });

        statusStream.onerror = () => {
          closeStatusStream();
        };
      }

      document.getElementById('start-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
          const payload = {
            origin: document.getElementById('origin').value,
            k: Number(document.getElementById('depth').value),
            workers: Number(document.getElementById('workers').value),
            queue_depth: Number(document.getElementById('queue').value),
            rps: Number(document.getElementById('rps').value)
          };
          const out = await api('POST', '/api/index', payload);
          document.getElementById('resume-run-id').value = out.run_id;
          document.getElementById('stop-run-id').value = out.run_id;
          openStatusStream(Number(out.run_id));
          await refreshRuns();
          await refreshStatus();
        } catch (err) {
          alert(err.message);
        }
      });

      document.getElementById('resume-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
          const val = document.getElementById('resume-run-id').value.trim();
          const payload = {};
          if (val) { payload.run_id = Number(val); }
          const out = await api('POST', '/api/resume', payload);
          document.getElementById('stop-run-id').value = out.run_id;
          openStatusStream(Number(out.run_id));
          await refreshRuns();
          await refreshStatus();
        } catch (err) {
          alert(err.message);
        }
      });

      document.getElementById('stop-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
          const runId = Number(document.getElementById('stop-run-id').value);
          const out = await api('POST', '/api/stop', { run_id: runId });
          const feedback = document.getElementById('stop-feedback');
          if (out.stop_requested) {
            feedback.style.color = '#0a6f3c';
            feedback.textContent = 'Stop requested for run ' + runId + '.';
          } else {
            feedback.style.color = '#9a5d00';
            feedback.textContent = 'Run ' + runId + ' is not active; nothing to stop.';
          }
          await refreshRuns();
          await refreshStatus();
        } catch (err) {
          const feedback = document.getElementById('stop-feedback');
          feedback.style.color = '#9d1c1c';
          feedback.textContent = 'Stop request failed: ' + err.message;
          alert(err.message);
        }
      });

      document.getElementById('search-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
          const query = encodeURIComponent(document.getElementById('query').value);
          const limit = Number(document.getElementById('limit').value);
          const sortBy = encodeURIComponent(document.getElementById('sort-by').value || 'relevance');
          const rows = await api('GET', '/api/search?q=' + query + '&limit=' + limit + '&sortBy=' + sortBy);
          setPre('search-results', rows);
        } catch (err) {
          alert(err.message);
        }
      });

      async function refreshAll() {
        try {
          await Promise.all([refreshRuns(), refreshStatus()]);
        } catch (err) {
          console.log(err.message);
        }
      }

      refreshAll();
      if (!window.EventSource) {
        setInterval(refreshAll, 2000);
      } else {
        setInterval(refreshRuns, 5000);
      }
    </script>
  </body>
</html>
"""


class WebHandler(BaseHTTPRequestHandler):
    app_state: WebAppState

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_headers(self) -> None:
      self.send_response(HTTPStatus.OK)
      self.send_header("Content-Type", "text/event-stream; charset=utf-8")
      self.send_header("Cache-Control", "no-cache")
      self.send_header("Connection", "keep-alive")
      self.end_headers()

    def _write_sse_event(self, event_name: str, payload: Any) -> None:
      chunk = f"event: {event_name}\n".encode("utf-8")
      self.wfile.write(chunk)
      data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
      self.wfile.write(b"data: ")
      self.wfile.write(data)
      self.wfile.write(b"\n\n")
      self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            body_len = int(raw_len)
        except ValueError as exc:
            raise ValueError("invalid content-length") from exc

        if body_len <= 0:
            return {}
        data = self.rfile.read(body_len)
        if not data:
            return {}

        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid json body") from exc

        if not isinstance(payload, dict):
            raise ValueError("json body must be an object")
        return payload

    def _bad_request(self, message: str) -> None:
        self._send_json({"error": message}, status=HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._send_html(DASHBOARD_HTML)
            return

        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/runs":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["20"])[0])
            except ValueError:
                self._bad_request("limit must be an integer")
                return
            self._send_json(self.app_state.list_runs(limit=max(1, limit)))
            return

        if parsed.path == "/api/status":
            params = parse_qs(parsed.query)
            run_id: int | None = None
            raw_run = params.get("run_id", [""])[0].strip()
            if raw_run:
                try:
                    run_id = int(raw_run)
                except ValueError:
                    self._bad_request("run_id must be an integer")
                    return

            payload = self.app_state.run_status(run_id)
            self._send_json(payload or {})
            return

        if parsed.path == "/api/events":
            params = parse_qs(parsed.query)
            requested_run_id: int | None = None
            raw_run = params.get("run_id", [""])[0].strip()
            if raw_run:
                try:
                    requested_run_id = int(raw_run)
                except ValueError:
                    self._bad_request("run_id must be an integer")
                    return

            self._send_sse_headers()
            self.close_connection = True
            try:
                for _ in range(300):
                    payload = self.app_state.run_status(requested_run_id) or {}
                    self._write_sse_event("status", payload)

                    run = payload.get("run") if isinstance(payload, dict) else None
                    active = bool(payload.get("active")) if isinstance(payload, dict) else False
                    run_status = str(run.get("status", "")) if isinstance(run, dict) else ""
                    if run_status in {"completed", "stopped", "failed"} and not active:
                        self._write_sse_event("done", payload)
                        return
                    time.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        if parsed.path == "/api/search":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            try:
                limit = int(params.get("limit", ["20"])[0])
            except ValueError:
                self._bad_request("limit must be an integer")
                return

            sort_by = params.get("sortBy", ["relevance"])[0].strip().lower() or "relevance"
            if sort_by not in {"relevance", "depth"}:
                self._bad_request("sortBy must be relevance or depth")
                return

            if not query.strip():
                self._bad_request("q parameter is required")
                return

            rows = self.app_state.search(query=query, limit=max(1, limit), sort_by=sort_by)
            self._send_json(rows)
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._bad_request(str(exc))
            return

        if parsed.path == "/api/index":
            try:
                origin = str(payload.get("origin", "")).strip()
                max_depth = int(payload.get("k", 2))
                workers = int(payload.get("workers", 8))
                queue_depth = int(payload.get("queue_depth", 500))
                requests_per_second = float(payload.get("rps", 5.0))
                allow_external_links = bool(payload.get("allow_external_links", False))
                status_interval = float(payload.get("status_interval", 1.0))
            except (TypeError, ValueError):
                self._bad_request("invalid index parameters")
                return

            if not origin:
                self._bad_request("origin is required")
                return
            if max_depth < 0:
                self._bad_request("k must be >= 0")
                return

            try:
                result = self.app_state.start_new_run(
                    origin=origin,
                    max_depth=max_depth,
                    workers=workers,
                    queue_depth=queue_depth,
                    requests_per_second=requests_per_second,
                    allow_external_links=allow_external_links,
                    status_interval=status_interval,
                )
            except ValueError as exc:
                self._bad_request(str(exc))
                return

            self._send_json(result, status=HTTPStatus.CREATED)
            return

        if parsed.path == "/api/resume":
            raw_run_id = payload.get("run_id")
            run_id: int | None
            try:
                run_id = int(raw_run_id) if raw_run_id is not None else None
                workers = int(payload.get("workers", 8))
                queue_depth = int(payload.get("queue_depth", 500))
                requests_per_second = float(payload.get("rps", 5.0))
                allow_external_links = bool(payload.get("allow_external_links", False))
                status_interval = float(payload.get("status_interval", 1.0))
            except (TypeError, ValueError):
                self._bad_request("invalid resume parameters")
                return

            try:
                result = self.app_state.resume_run(
                    run_id=run_id,
                    workers=workers,
                    queue_depth=queue_depth,
                    requests_per_second=requests_per_second,
                    allow_external_links=allow_external_links,
                    status_interval=status_interval,
                )
            except ValueError as exc:
                self._bad_request(str(exc))
                return

            self._send_json(result)
            return

        if parsed.path == "/api/stop":
            try:
                run_id = int(payload.get("run_id"))
            except (TypeError, ValueError):
                self._bad_request("run_id must be an integer")
                return

            stopped = self.app_state.stop_run(run_id)
            self._send_json({"run_id": run_id, "stop_requested": stopped})
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def build_handler(app_state: WebAppState) -> type[WebHandler]:
    class BoundHandler(WebHandler):
        pass

    BoundHandler.app_state = app_state
    return BoundHandler


def create_server(db_path: str, host: str, port: int) -> ThreadingHTTPServer:
    app_state = WebAppState(db_path)
    return ThreadingHTTPServer((host, port), build_handler(app_state))


def serve_web(db_path: str, host: str, port: int) -> None:
    server = create_server(db_path=db_path, host=host, port=port)
    actual_host, actual_port = server.server_address
    print(f"web server listening on http://{actual_host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
