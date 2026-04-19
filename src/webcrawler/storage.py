from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CrawlTask, SearchTriple


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return conn

    def close_thread_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _init_schema(self) -> None:
        conn = self._connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin_url TEXT NOT NULL,
                max_depth INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS frontier (
                run_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                depth INTEGER NOT NULL,
                state TEXT NOT NULL,
                discovered_from TEXT,
                enqueued_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, url),
                FOREIGN KEY (run_id) REFERENCES crawl_runs(id)
            );

            CREATE TABLE IF NOT EXISTS run_discoveries (
                run_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                depth INTEGER NOT NULL,
                discovered_at TEXT NOT NULL,
                PRIMARY KEY (run_id, url),
                FOREIGN KEY (run_id) REFERENCES crawl_runs(id)
            );

            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY,
                title TEXT,
                body TEXT,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS page_terms (
                term TEXT NOT NULL,
                url TEXT NOT NULL,
                tf REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (term, url),
                FOREIGN KEY (url) REFERENCES pages(url)
            );

            CREATE TABLE IF NOT EXISTS runtime_state (
                run_id INTEGER PRIMARY KEY,
                queue_depth INTEGER NOT NULL,
                queue_capacity INTEGER NOT NULL,
                active_workers INTEGER NOT NULL,
                throttled_events INTEGER NOT NULL,
                last_heartbeat TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES crawl_runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_frontier_state ON frontier (run_id, state, depth, enqueued_at);
            CREATE INDEX IF NOT EXISTS idx_run_discoveries_url ON run_discoveries (url);
            CREATE INDEX IF NOT EXISTS idx_page_terms_url ON page_terms (url);
            CREATE INDEX IF NOT EXISTS idx_page_terms_term ON page_terms (term);
            """
        )
        conn.commit()

    def create_run(self, origin_url: str, max_depth: int) -> int:
        now = utc_now()
        with self._write_lock:
            conn = self._connection()
            cur = conn.execute(
                """
                INSERT INTO crawl_runs (origin_url, max_depth, status, created_at, updated_at)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (origin_url, max_depth, now, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        row = self._connection().execute(
            "SELECT * FROM crawl_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._connection().execute(
            "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_run_id(self) -> int | None:
        row = self._connection().execute("SELECT id FROM crawl_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return int(row["id"])

    def set_run_status(self, run_id: int, status: str, last_error: str | None = None) -> None:
        with self._write_lock:
            conn = self._connection()
            conn.execute(
                """
                UPDATE crawl_runs
                SET status = ?, updated_at = ?, last_error = ?
                WHERE id = ?
                """,
                (status, utc_now(), last_error, run_id),
            )
            conn.commit()

    def add_discovery(self, run_id: int, url: str, depth: int) -> bool:
        with self._write_lock:
            conn = self._connection()
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO run_discoveries (run_id, url, depth, discovered_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, url, depth, utc_now()),
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_seed(self, run_id: int, origin_url: str) -> None:
        self.discover_and_enqueue(run_id, origin_url, 0, discovered_from=None)

    def discover_and_enqueue(self, run_id: int, url: str, depth: int, discovered_from: str | None) -> bool:
        now = utc_now()
        with self._write_lock:
            conn = self._connection()
            discovery_cur = conn.execute(
                """
                INSERT OR IGNORE INTO run_discoveries (run_id, url, depth, discovered_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, url, depth, now),
            )
            inserted = discovery_cur.rowcount > 0
            if inserted:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO frontier (run_id, url, depth, state, discovered_from, enqueued_at, updated_at)
                    VALUES (?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (run_id, url, depth, discovered_from, now, now),
                )
            conn.commit()
            return inserted

    def enqueue_frontier(self, run_id: int, url: str, depth: int, discovered_from: str | None) -> bool:
        now = utc_now()
        with self._write_lock:
            conn = self._connection()
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO frontier (run_id, url, depth, state, discovered_from, enqueued_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (run_id, url, depth, discovered_from, now, now),
            )
            conn.commit()
            return cur.rowcount > 0

    def claim_queued_tasks(self, run_id: int, limit: int) -> list[CrawlTask]:
        if limit <= 0:
            return []

        with self._write_lock:
            conn = self._connection()
            rows = conn.execute(
                """
                SELECT url, depth
                FROM frontier
                WHERE run_id = ? AND state = 'queued'
                ORDER BY depth ASC, enqueued_at ASC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()

            tasks: list[CrawlTask] = []
            now = utc_now()
            for row in rows:
                cur = conn.execute(
                    """
                    UPDATE frontier
                    SET state = 'processing', updated_at = ?
                    WHERE run_id = ? AND url = ? AND state = 'queued'
                    """,
                    (now, run_id, row["url"]),
                )
                if cur.rowcount > 0:
                    tasks.append(CrawlTask(url=str(row["url"]), depth=int(row["depth"])))

            conn.commit()
            return tasks

    def requeue_processing_tasks(self, run_id: int) -> None:
        with self._write_lock:
            conn = self._connection()
            conn.execute(
                """
                UPDATE frontier
                SET state = 'queued', updated_at = ?
                WHERE run_id = ? AND state = 'processing'
                """,
                (utc_now(), run_id),
            )
            conn.commit()

    def mark_task_complete(self, run_id: int, url: str, failed: bool = False) -> None:
        next_state = "failed" if failed else "completed"
        with self._write_lock:
            conn = self._connection()
            conn.execute(
                """
                UPDATE frontier
                SET state = ?, updated_at = ?
                WHERE run_id = ? AND url = ?
                """,
                (next_state, utc_now(), run_id, url),
            )
            conn.commit()

    def pending_count(self, run_id: int) -> int:
        row = self._connection().execute(
            """
            SELECT COUNT(*) AS c
            FROM frontier
            WHERE run_id = ? AND state IN ('queued', 'processing')
            """,
            (run_id,),
        ).fetchone()
        return int(row["c"]) if row else 0

    def frontier_counts(self, run_id: int) -> dict[str, int]:
        rows = self._connection().execute(
            """
            SELECT state, COUNT(*) AS c
            FROM frontier
            WHERE run_id = ?
            GROUP BY state
            """,
            (run_id,),
        ).fetchall()

        counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0}
        for row in rows:
            counts[str(row["state"])] = int(row["c"])
        return counts

    def upsert_page_and_terms(self, url: str, title: str, body: str, term_freq: dict[str, float]) -> None:
        content_hash = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
        now = utc_now()

        with self._write_lock:
            conn = self._connection()
            conn.execute(
                """
                INSERT INTO pages (url, title, body, content_hash, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    content_hash = excluded.content_hash,
                    fetched_at = excluded.fetched_at
                """,
                (url, title, body, content_hash, now),
            )

            conn.execute("DELETE FROM page_terms WHERE url = ?", (url,))
            if term_freq:
                conn.executemany(
                    """
                    INSERT INTO page_terms (term, url, tf, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(term, url, tf, now) for term, tf in term_freq.items()],
                )
            conn.commit()

    def record_runtime_state(
        self,
        run_id: int,
        queue_depth: int,
        queue_capacity: int,
        active_workers: int,
        throttled_events: int,
    ) -> None:
        with self._write_lock:
            conn = self._connection()
            conn.execute(
                """
                INSERT INTO runtime_state (
                    run_id, queue_depth, queue_capacity, active_workers, throttled_events, last_heartbeat
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    queue_depth = excluded.queue_depth,
                    queue_capacity = excluded.queue_capacity,
                    active_workers = excluded.active_workers,
                    throttled_events = excluded.throttled_events,
                    last_heartbeat = excluded.last_heartbeat
                """,
                (run_id, queue_depth, queue_capacity, active_workers, throttled_events, utc_now()),
            )
            conn.commit()

    def get_runtime_state(self, run_id: int) -> dict[str, Any] | None:
        row = self._connection().execute(
            "SELECT * FROM runtime_state WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def search(self, tokens: list[str], limit: int = 20) -> list[SearchTriple]:
        deduped = sorted(set(tokens))
        if not deduped:
            return []

        placeholders = ",".join(["?"] * len(deduped))
        sql = f"""
            SELECT
                rd.url AS relevant_url,
                cr.origin_url AS origin_url,
                rd.depth AS depth,
                SUM(pt.tf) AS ranking_score,
                COUNT(DISTINCT pt.term) AS matched_terms
            FROM run_discoveries rd
            JOIN crawl_runs cr ON cr.id = rd.run_id
            JOIN page_terms pt ON pt.url = rd.url
            WHERE pt.term IN ({placeholders})
            GROUP BY rd.run_id, rd.url, cr.origin_url, rd.depth
            HAVING COUNT(DISTINCT pt.term) = ?
            ORDER BY ranking_score DESC, rd.depth ASC, relevant_url ASC
            LIMIT ?
        """
        params: tuple[Any, ...] = (*deduped, len(deduped), max(limit, 1))

        rows = self._connection().execute(sql, params).fetchall()
        return [
            SearchTriple(
                relevant_url=str(row["relevant_url"]),
                origin_url=str(row["origin_url"]),
                depth=int(row["depth"]),
            )
            for row in rows
        ]

    def run_status(self, run_id: int) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None

        counts = self.frontier_counts(run_id)
        discovered_row = self._connection().execute(
            "SELECT COUNT(*) AS c FROM run_discoveries WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        runtime = self.get_runtime_state(run_id)
        return {
            "run": run,
            "discovered_urls": int(discovered_row["c"]) if discovered_row else 0,
            "frontier": counts,
            "runtime": runtime,
        }
