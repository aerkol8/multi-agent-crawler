from __future__ import annotations

import queue
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import CrawlTask
from .storage import Storage
from .utils import extract_links_and_text, term_frequencies, tokenize


@dataclass
class RuntimeSnapshot:
    run_id: int
    queue_depth: int
    queue_capacity: int
    active_workers: int
    throttled_events: int
    pending_tasks: int


class TokenBucketRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.rate = max(0.0, requests_per_second)
        self.capacity = max(1.0, self.rate) if self.rate > 0 else 1.0
        self.tokens = self.capacity
        self.updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.rate <= 0:
            return

        while True:
            wait_for = 0.0
            with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self.updated_at)
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.updated_at = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait_for = (1.0 - self.tokens) / self.rate

            time.sleep(min(max(wait_for, 0.01), 0.25))


class CrawlerEngine:
    def __init__(
        self,
        storage: Storage,
        run_id: int,
        origin_url: str,
        max_depth: int,
        workers: int,
        pending_limit: int,
        requests_per_second: float,
        same_host_only: bool,
        status_interval_seconds: float = 1.0,
        status_hook: Callable[[RuntimeSnapshot], None] | None = None,
    ) -> None:
        self.storage = storage
        self.run_id = run_id
        self.origin_url = origin_url
        self.max_depth = max_depth
        self.pending_limit = max(1, pending_limit)
        self.same_host_only = same_host_only
        self.status_interval_seconds = max(0.2, status_interval_seconds)
        self.status_hook = status_hook

        self._work_queue: queue.Queue[CrawlTask] = queue.Queue(maxsize=max(4, workers * 2))
        self._stop_event = threading.Event()
        self._workers_count = max(1, workers)
        self._active_workers = 0
        self._active_lock = threading.Lock()

        self._throttled_events = 0
        self._throttle_lock = threading.Lock()

        self._limiter = TokenBucketRateLimiter(requests_per_second)
        self._origin_host = (urlparse(origin_url).hostname or "").lower()

        self._feeder_thread: threading.Thread | None = None
        self._worker_threads: list[threading.Thread] = []

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> bool:
        completed = False
        interrupted = False
        self.storage.set_run_status(self.run_id, "running")

        self._feeder_thread = threading.Thread(target=self._feeder_loop, name=f"feeder-{self.run_id}", daemon=True)
        self._feeder_thread.start()

        for idx in range(self._workers_count):
            worker = threading.Thread(target=self._worker_loop, name=f"worker-{self.run_id}-{idx}", daemon=True)
            worker.start()
            self._worker_threads.append(worker)

        try:
            while not self._stop_event.is_set():
                self._emit_runtime_snapshot()
                if self._is_complete():
                    completed = True
                    break
                time.sleep(self.status_interval_seconds)
        except KeyboardInterrupt:
            interrupted = True
            self._stop_event.set()
        except Exception as exc:
            self.storage.set_run_status(self.run_id, "failed", last_error=str(exc))
            self._stop_event.set()
            raise
        finally:
            self._stop_event.set()
            if self._feeder_thread is not None:
                self._feeder_thread.join(timeout=3)
            for worker in self._worker_threads:
                worker.join(timeout=3)

            if not completed:
                completed = self._is_complete()

            if completed:
                self.storage.set_run_status(self.run_id, "completed")
            elif interrupted:
                self.storage.set_run_status(self.run_id, "stopped")
            else:
                current = self.storage.get_run(self.run_id)
                if current and current.get("status") == "running":
                    self.storage.set_run_status(self.run_id, "stopped")

            self._emit_runtime_snapshot()
            self.storage.close_thread_connection()

        return completed

    def _feeder_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                free_slots = self._work_queue.maxsize - self._work_queue.qsize()
                if free_slots <= 0:
                    time.sleep(0.05)
                    continue

                tasks = self.storage.claim_queued_tasks(self.run_id, min(free_slots, 64))
                if not tasks:
                    if self._is_complete():
                        break
                    time.sleep(0.1)
                    continue

                for task in tasks:
                    while not self._stop_event.is_set():
                        try:
                            self._work_queue.put(task, timeout=0.2)
                            break
                        except queue.Full:
                            continue
        finally:
            self.storage.close_thread_connection()

    def _worker_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    task = self._work_queue.get(timeout=0.25)
                except queue.Empty:
                    if self._is_complete():
                        break
                    continue

                self._inc_active_workers()
                try:
                    self._handle_task(task)
                finally:
                    self._dec_active_workers()
                    self._work_queue.task_done()
        finally:
            self.storage.close_thread_connection()

    def _handle_task(self, task: CrawlTask) -> None:
        failed = False
        try:
            html, fetched_url = self._fetch(task.url)
            links, text, title = extract_links_and_text(html, fetched_url)
            combined_text = " ".join(part for part in [title, text] if part)
            terms = term_frequencies(tokenize(combined_text))
            self.storage.upsert_page_and_terms(task.url, title, combined_text, terms)

            if task.depth < self.max_depth:
                next_depth = task.depth + 1
                for link in links:
                    if self.same_host_only and self._origin_host:
                        link_host = (urlparse(link).hostname or "").lower()
                        if link_host != self._origin_host:
                            continue
                    self._wait_for_backpressure()
                    if self._stop_event.is_set():
                        break
                    self.storage.discover_and_enqueue(
                        run_id=self.run_id,
                        url=link,
                        depth=next_depth,
                        discovered_from=task.url,
                    )
        except Exception:
            failed = True
        finally:
            self.storage.mark_task_complete(self.run_id, task.url, failed=failed)

    def _wait_for_backpressure(self) -> None:
        waited_seconds = 0.0
        while not self._stop_event.is_set():
            pending = self.storage.pending_count(self.run_id)
            effective_pending = max(0, pending - 1)
            if effective_pending < self.pending_limit:
                return

            # If every worker is already busy, strict waiting can self-deadlock.
            # This escape hatch preserves progress while still counting throttle events.
            if self._active_workers >= self._workers_count:
                with self._throttle_lock:
                    self._throttled_events += 1
                if self._workers_count <= 1 or waited_seconds >= 2.0:
                    return

            with self._throttle_lock:
                self._throttled_events += 1
            time.sleep(0.05)
            waited_seconds += 0.05

    def _fetch(self, url: str) -> tuple[str, str]:
        last_exc: Exception | None = None
        for attempt in range(3):
            self._limiter.acquire()
            request = Request(url, headers={"User-Agent": "webcrawler-multiagent/1.0"})
            try:
                with urlopen(request, timeout=10) as response:
                    content_type = response.headers.get("Content-Type", "")
                    raw = response.read(2_000_000)
                    charset = response.headers.get_content_charset() or "utf-8"
                    text = raw.decode(charset, errors="ignore")
                    if "text/html" not in content_type and "text/" not in content_type:
                        text = ""
                    return text, response.geturl()
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                time.sleep(0.2 * (2**attempt))

        raise RuntimeError(f"failed to fetch {url}: {last_exc}")

    def _is_complete(self) -> bool:
        pending = self.storage.pending_count(self.run_id)
        if pending > 0:
            return False
        if not self._work_queue.empty():
            return False
        return self._active_workers == 0

    def _emit_runtime_snapshot(self) -> RuntimeSnapshot:
        with self._throttle_lock:
            throttled = self._throttled_events
        snapshot = RuntimeSnapshot(
            run_id=self.run_id,
            queue_depth=self.storage.pending_count(self.run_id),
            queue_capacity=self.pending_limit,
            active_workers=self._active_workers,
            throttled_events=throttled,
            pending_tasks=self.storage.pending_count(self.run_id),
        )
        self.storage.record_runtime_state(
            run_id=self.run_id,
            queue_depth=snapshot.queue_depth,
            queue_capacity=snapshot.queue_capacity,
            active_workers=snapshot.active_workers,
            throttled_events=snapshot.throttled_events,
        )
        if self.status_hook is not None:
            self.status_hook(snapshot)
        return snapshot

    def _inc_active_workers(self) -> None:
        with self._active_lock:
            self._active_workers += 1

    def _dec_active_workers(self) -> None:
        with self._active_lock:
            self._active_workers = max(0, self._active_workers - 1)

    def runtime_snapshot_dict(self) -> dict[str, int]:
        snapshot = self._emit_runtime_snapshot()
        return asdict(snapshot)
