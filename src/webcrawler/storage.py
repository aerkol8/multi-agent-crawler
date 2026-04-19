from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CrawlTask, SearchHit


TERM_DATA_BUCKETS: tuple[str, ...] = tuple("0123456789abcdefghijklmnopqrstuvwxyz_")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Storage:
    _locks_guard = threading.Lock()
    _shared_locks: dict[str, threading.RLock] = {}

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

        self._root = self._resolve_root(Path(db_path))
        self._root.mkdir(parents=True, exist_ok=True)

        root_key = str(self._root.resolve())
        with self._locks_guard:
            existing = self._shared_locks.get(root_key)
            if existing is None:
                existing = threading.RLock()
                self._shared_locks[root_key] = existing
            self._write_lock = existing

        self._runs_file = self._root / "runs.jsonl"
        self._frontier_file = self._root / "frontier.jsonl"
        self._discoveries_file = self._root / "discoveries.jsonl"
        self._pages_file = self._root / "pages.jsonl"
        self._terms_file = self._root / "terms.jsonl"
        self._runtime_file = self._root / "runtime.jsonl"
        self._term_data_dir = self._root

        self._ensure_files()

        self._runs: dict[int, dict[str, Any]] = {}
        self._frontier: dict[int, dict[str, dict[str, Any]]] = {}
        self._discoveries: dict[int, dict[str, int]] = {}
        self._pages: dict[str, dict[str, Any]] = {}
        self._terms_by_url: dict[str, dict[str, float]] = {}
        self._runtime_by_run: dict[int, dict[str, Any]] = {}
        self._next_run_id = 1
        self._term_data_dirty = False
        self._last_term_data_rewrite_monotonic = 0.0
        self._term_data_rewrite_interval_seconds = 0.75

        self._load_state()

    def _resolve_root(self, requested: Path) -> Path:
        if requested.exists() and requested.is_file():
            return requested.with_suffix(requested.suffix + ".fs")
        return requested

    def _ensure_files(self) -> None:
        for path in (
            self._runs_file,
            self._frontier_file,
            self._discoveries_file,
            self._pages_file,
            self._terms_file,
            self._runtime_file,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")

        self._term_data_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_legacy_term_data_locked()

    def _cleanup_legacy_term_data_locked(self) -> None:
        legacy_dir = self._root / "data" / "storage"
        if not legacy_dir.exists():
            return

        for existing in list(legacy_dir.glob("*.data")):
            existing.unlink(missing_ok=True)

        try:
            legacy_dir.rmdir()
        except OSError:
            return

        legacy_parent = legacy_dir.parent
        try:
            if legacy_parent.exists() and not any(legacy_parent.iterdir()):
                legacy_parent.rmdir()
        except OSError:
            return

    def _append_json_line(self, path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _iter_json_lines(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not path.exists():
            return rows

        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows

    def _load_state(self) -> None:
        for row in self._iter_json_lines(self._runs_file):
            run = row.get("run")
            if not isinstance(run, dict):
                continue
            try:
                run_id = int(run["id"])
            except (KeyError, TypeError, ValueError):
                continue
            self._runs[run_id] = dict(run)

        for row in self._iter_json_lines(self._discoveries_file):
            op = str(row.get("op", "upsert"))
            try:
                run_id = int(row["run_id"])
                url = str(row["url"])
            except (KeyError, TypeError, ValueError):
                continue

            discovered = self._discoveries.setdefault(run_id, {})
            if op == "delete":
                discovered.pop(url, None)
                continue

            try:
                depth = int(row.get("depth", 0))
            except (TypeError, ValueError):
                depth = 0
            discovered[url] = depth

        for row in self._iter_json_lines(self._frontier_file):
            try:
                run_id = int(row["run_id"])
                url = str(row["url"])
            except (KeyError, TypeError, ValueError):
                continue

            run_frontier = self._frontier.setdefault(run_id, {})
            state = str(row.get("state", "queued"))
            try:
                depth = int(row.get("depth", 0))
            except (TypeError, ValueError):
                depth = 0
            run_frontier[url] = {
                "run_id": run_id,
                "url": url,
                "depth": depth,
                "state": state,
                "discovered_from": row.get("discovered_from"),
                "enqueued_at": str(row.get("enqueued_at", utc_now())),
                "updated_at": str(row.get("updated_at", utc_now())),
            }

        for row in self._iter_json_lines(self._pages_file):
            op = str(row.get("op", "upsert"))
            try:
                url = str(row["url"])
            except (KeyError, TypeError, ValueError):
                continue

            if op == "delete":
                self._pages.pop(url, None)
                continue

            self._pages[url] = {
                "url": url,
                "title": str(row.get("title", "")),
                "body": str(row.get("body", "")),
                "content_hash": str(row.get("content_hash", "")),
                "fetched_at": str(row.get("fetched_at", utc_now())),
            }

        for row in self._iter_json_lines(self._terms_file):
            op = str(row.get("op", "upsert"))
            try:
                url = str(row["url"])
            except (KeyError, TypeError, ValueError):
                continue

            if op == "delete":
                self._terms_by_url.pop(url, None)
                continue

            raw_terms = row.get("terms")
            if not isinstance(raw_terms, dict):
                continue

            term_map: dict[str, float] = {}
            for key, value in raw_terms.items():
                try:
                    term_map[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            self._terms_by_url[url] = term_map

        for row in self._iter_json_lines(self._runtime_file):
            try:
                run_id = int(row["run_id"])
            except (KeyError, TypeError, ValueError):
                continue
            self._runtime_by_run[run_id] = {
                "run_id": run_id,
                "queue_depth": int(row.get("queue_depth", 0) or 0),
                "queue_capacity": int(row.get("queue_capacity", 0) or 0),
                "active_workers": int(row.get("active_workers", 0) or 0),
                "throttled_events": int(row.get("throttled_events", 0) or 0),
                "last_heartbeat": str(row.get("last_heartbeat", utc_now())),
            }

        if self._runs:
            self._next_run_id = max(self._runs.keys()) + 1

        if not self._term_data_files_ready_locked():
            self._rewrite_p_data_locked()
            self._last_term_data_rewrite_monotonic = time.monotonic()

    def _term_data_files_ready_locked(self) -> bool:
        return (self._term_data_dir / "all.data").exists()

    def _mark_term_data_dirty_locked(self) -> None:
        self._term_data_dirty = True

    def _flush_term_data_if_needed_locked(self, force: bool = False) -> None:
        if not self._term_data_dirty:
            return

        now = time.monotonic()
        if not force and (now - self._last_term_data_rewrite_monotonic) < self._term_data_rewrite_interval_seconds:
            return

        self._rewrite_p_data_locked()
        self._term_data_dirty = False
        self._last_term_data_rewrite_monotonic = now

    def close_thread_connection(self) -> None:
        with self._write_lock:
            self._flush_term_data_if_needed_locked(force=True)

    def _write_run(self, run: dict[str, Any]) -> None:
        self._append_json_line(self._runs_file, {"event": "run", "run": run})

    def create_run(self, origin_url: str, max_depth: int) -> int:
        now = utc_now()
        with self._write_lock:
            run_id = self._next_run_id
            self._next_run_id += 1
            run = {
                "id": run_id,
                "origin_url": origin_url,
                "max_depth": int(max_depth),
                "status": "running",
                "created_at": now,
                "updated_at": now,
                "last_error": None,
            }
            self._runs[run_id] = dict(run)
            self._write_run(run)
            return run_id

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        return dict(run) if run else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = sorted(self._runs.values(), key=lambda row: int(row["id"]), reverse=True)
        return [dict(row) for row in rows[: max(1, limit)]]

    def latest_run_id(self) -> int | None:
        if not self._runs:
            return None
        return max(self._runs.keys())

    def set_run_status(self, run_id: int, status: str, last_error: str | None = None) -> None:
        with self._write_lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run["status"] = status
            run["updated_at"] = utc_now()
            run["last_error"] = last_error
            self._write_run(dict(run))
            if status in {"completed", "stopped", "failed"}:
                self._flush_term_data_if_needed_locked(force=True)

    def add_discovery(self, run_id: int, url: str, depth: int) -> bool:
        return self.discover_and_enqueue(run_id, url, depth, discovered_from=None)

    def insert_seed(self, run_id: int, origin_url: str) -> None:
        self.discover_and_enqueue(run_id, origin_url, 0, discovered_from=None)

    def discover_and_enqueue(self, run_id: int, url: str, depth: int, discovered_from: str | None) -> bool:
        now = utc_now()
        with self._write_lock:
            discovered = self._discoveries.setdefault(run_id, {})
            if url in discovered:
                return False

            discovered[url] = int(depth)
            self._append_json_line(
                self._discoveries_file,
                {
                    "event": "discovery",
                    "op": "upsert",
                    "run_id": run_id,
                    "url": url,
                    "depth": int(depth),
                    "discovered_at": now,
                },
            )

            frontier = self._frontier.setdefault(run_id, {})
            record = {
                "run_id": run_id,
                "url": url,
                "depth": int(depth),
                "state": "queued",
                "discovered_from": discovered_from,
                "enqueued_at": now,
                "updated_at": now,
            }
            frontier[url] = record
            self._append_json_line(self._frontier_file, {"event": "frontier", **record})
            return True

    def enqueue_frontier(self, run_id: int, url: str, depth: int, discovered_from: str | None) -> bool:
        now = utc_now()
        with self._write_lock:
            frontier = self._frontier.setdefault(run_id, {})
            if url in frontier and frontier[url].get("state") != "deleted":
                return False

            record = {
                "run_id": run_id,
                "url": url,
                "depth": int(depth),
                "state": "queued",
                "discovered_from": discovered_from,
                "enqueued_at": now,
                "updated_at": now,
            }
            frontier[url] = record
            self._append_json_line(self._frontier_file, {"event": "frontier", **record})
            return True

    def claim_queued_tasks(self, run_id: int, limit: int) -> list[CrawlTask]:
        if limit <= 0:
            return []

        with self._write_lock:
            frontier = self._frontier.get(run_id, {})
            queued = [
                row
                for row in frontier.values()
                if str(row.get("state")) == "queued"
            ]
            queued.sort(key=lambda row: (int(row.get("depth", 0)), str(row.get("enqueued_at", "")), str(row.get("url", ""))))

            tasks: list[CrawlTask] = []
            now = utc_now()
            for row in queued[:limit]:
                url = str(row["url"])
                current = frontier.get(url)
                if current is None or str(current.get("state")) != "queued":
                    continue

                current["state"] = "processing"
                current["updated_at"] = now
                self._append_json_line(self._frontier_file, {"event": "frontier", **current})
                tasks.append(CrawlTask(url=url, depth=int(current.get("depth", 0))))

            return tasks

    def requeue_processing_tasks(self, run_id: int) -> None:
        with self._write_lock:
            frontier = self._frontier.get(run_id, {})
            now = utc_now()
            for row in frontier.values():
                if str(row.get("state")) != "processing":
                    continue
                row["state"] = "queued"
                row["updated_at"] = now
                self._append_json_line(self._frontier_file, {"event": "frontier", **row})

    def mark_task_complete(self, run_id: int, url: str, failed: bool = False) -> None:
        with self._write_lock:
            frontier = self._frontier.get(run_id, {})
            row = frontier.get(url)
            if row is None:
                return
            row["state"] = "failed" if failed else "completed"
            row["updated_at"] = utc_now()
            self._append_json_line(self._frontier_file, {"event": "frontier", **row})

    def pending_count(self, run_id: int) -> int:
        frontier = self._frontier.get(run_id, {})
        return sum(1 for row in frontier.values() if str(row.get("state")) in {"queued", "processing"})

    def frontier_counts(self, run_id: int) -> dict[str, int]:
        counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0}
        frontier = self._frontier.get(run_id, {})
        for row in frontier.values():
            state = str(row.get("state"))
            if state in counts:
                counts[state] += 1
        return counts

    def upsert_page_and_terms(self, url: str, title: str, body: str, term_freq: dict[str, float]) -> None:
        content_hash = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
        now = utc_now()
        clean_terms = {str(term): float(freq) for term, freq in term_freq.items()}

        with self._write_lock:
            self._pages[url] = {
                "url": url,
                "title": title,
                "body": body,
                "content_hash": content_hash,
                "fetched_at": now,
            }
            self._terms_by_url[url] = clean_terms

            self._append_json_line(
                self._pages_file,
                {
                    "event": "page",
                    "op": "upsert",
                    "url": url,
                    "title": title,
                    "body": body,
                    "content_hash": content_hash,
                    "fetched_at": now,
                },
            )
            self._append_json_line(
                self._terms_file,
                {
                    "event": "terms",
                    "op": "upsert",
                    "url": url,
                    "terms": clean_terms,
                    "updated_at": now,
                },
            )
            self._mark_term_data_dirty_locked()
            self._flush_term_data_if_needed_locked()

    def delete_url(self, url: str, run_id: int | None = None) -> dict[str, int]:
        with self._write_lock:
            deleted_frontier = 0
            deleted_discoveries = 0

            candidate_runs = [run_id] if run_id is not None else sorted(self._runs.keys())
            now = utc_now()

            for current_run_id in candidate_runs:
                if current_run_id is None:
                    continue

                discoveries = self._discoveries.get(current_run_id, {})
                if url in discoveries:
                    discoveries.pop(url, None)
                    deleted_discoveries += 1
                    self._append_json_line(
                        self._discoveries_file,
                        {
                            "event": "discovery",
                            "op": "delete",
                            "run_id": current_run_id,
                            "url": url,
                            "deleted_at": now,
                        },
                    )

                frontier = self._frontier.get(current_run_id, {})
                record = frontier.get(url)
                if record is not None and str(record.get("state")) != "deleted":
                    deleted_frontier += 1
                    record["state"] = "deleted"
                    record["updated_at"] = now
                    self._append_json_line(self._frontier_file, {"event": "frontier", **record})

            still_referenced = any(url in discovered for discovered in self._discoveries.values())

            deleted_terms = 0
            deleted_pages = 0
            if not still_referenced:
                if url in self._terms_by_url:
                    deleted_terms = len(self._terms_by_url.get(url, {}))
                    self._terms_by_url.pop(url, None)
                    self._append_json_line(
                        self._terms_file,
                        {
                            "event": "terms",
                            "op": "delete",
                            "url": url,
                            "updated_at": now,
                        },
                    )

                if url in self._pages:
                    deleted_pages = 1
                    self._pages.pop(url, None)
                    self._append_json_line(
                        self._pages_file,
                        {
                            "event": "page",
                            "op": "delete",
                            "url": url,
                            "updated_at": now,
                        },
                    )

            self._mark_term_data_dirty_locked()
            self._flush_term_data_if_needed_locked()

            return {
                "deleted_frontier": deleted_frontier,
                "deleted_discoveries": deleted_discoveries,
                "deleted_terms": deleted_terms,
                "deleted_pages": deleted_pages,
            }

    def record_runtime_state(
        self,
        run_id: int,
        queue_depth: int,
        queue_capacity: int,
        active_workers: int,
        throttled_events: int,
    ) -> None:
        with self._write_lock:
            row = {
                "run_id": int(run_id),
                "queue_depth": int(queue_depth),
                "queue_capacity": int(queue_capacity),
                "active_workers": int(active_workers),
                "throttled_events": int(throttled_events),
                "last_heartbeat": utc_now(),
            }
            self._runtime_by_run[int(run_id)] = row
            self._append_json_line(self._runtime_file, {"event": "runtime", **row})

    def get_runtime_state(self, run_id: int) -> dict[str, Any] | None:
        row = self._runtime_by_run.get(run_id)
        return dict(row) if row else None

    def search(self, tokens: list[str], limit: int = 20, sort_by: str = "relevance") -> list[SearchHit]:
        deduped = sorted({token for token in tokens if token})
        if not deduped:
            return []

        rows: list[SearchHit] = []
        word_value = " ".join(deduped)

        for run_id, discovered in self._discoveries.items():
            run = self._runs.get(run_id)
            if run is None:
                continue
            origin = str(run.get("origin_url", ""))

            for url, depth in discovered.items():
                terms = self._terms_by_url.get(url)
                if not terms:
                    continue

                if not all(term in terms for term in deduped):
                    continue

                matched_freq = sum(float(terms[term]) for term in deduped)
                freq_value = round(matched_freq, 6)
                score = round((freq_value * 10.0) + 1000.0 - (int(depth) * 5.0), 6)
                rows.append(
                    SearchHit(
                        word=word_value,
                        url=url,
                        origin=origin,
                        depth=int(depth),
                        freq=freq_value,
                        score=score,
                    )
                )

        sort_mode = (sort_by or "relevance").strip().lower()
        if sort_mode == "depth":
            rows.sort(key=lambda row: (row.depth, -row.score, row.url))
        else:
            rows.sort(key=lambda row: (-row.score, row.depth, row.url))

        return rows[: max(1, int(limit))]

    def run_status(self, run_id: int) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None

        counts = self.frontier_counts(run_id)
        discovered_urls = len(self._discoveries.get(run_id, {}))
        runtime = self.get_runtime_state(run_id)

        return {
            "run": run,
            "discovered_urls": discovered_urls,
            "frontier": counts,
            "runtime": runtime,
        }

    def _rewrite_p_data_locked(self) -> None:
        lines_by_bucket: dict[str, list[str]] = {bucket: [] for bucket in TERM_DATA_BUCKETS}
        all_lines: list[str] = []

        for run_id in sorted(self._discoveries.keys()):
            run = self._runs.get(run_id)
            if run is None:
                continue
            origin = str(run.get("origin_url", ""))
            discovered = self._discoveries.get(run_id, {})
            for url in sorted(discovered.keys()):
                depth = int(discovered[url])
                terms = self._terms_by_url.get(url, {})
                for term in sorted(terms.keys()):
                    freq = float(terms[term])
                    line = f"{term} {url} {origin} {depth} {freq:.6f}"
                    all_lines.append(line)

                    bucket = term[0].lower() if term else "_"
                    if bucket not in lines_by_bucket:
                        bucket = "_"
                    lines_by_bucket[bucket].append(line)

        payloads: dict[str, str] = {}
        for bucket in TERM_DATA_BUCKETS:
            rows = lines_by_bucket.get(bucket, [])
            payloads[bucket] = "\n".join(rows) + ("\n" if rows else "")

        payloads["all"] = "\n".join(all_lines) + ("\n" if all_lines else "")

        target_dir = self._term_data_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        expected_names = {f"{bucket}.data" for bucket in payloads.keys()}
        for existing in target_dir.glob("*.data"):
            if existing.name in expected_names:
                continue
            existing.unlink(missing_ok=True)

        for bucket, payload in payloads.items():
            target = target_dir / f"{bucket}.data"
            temp_path = target.with_suffix(target.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, target)

        self._cleanup_legacy_term_data_locked()
