from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class DemoHandler(BaseHTTPRequestHandler):
    routes = {
        "/": "<html><body><a href='/a'>A</a><a href='/b'>B</a>seed page</body></html>",
        "/a": "<html><body>python crawler assignment demo</body></html>",
        "/b": "<html><body><a href='/c'>C</a>streaming index</body></html>",
        "/c": "<html><body>search while indexing still running</body></html>",
    }
    delays = {"/b": 1.0, "/c": 1.0}

    def do_GET(self) -> None:  # noqa: N802
        payload = self.routes.get(self.path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return

        delay = self.delays.get(self.path, 0.0)
        if delay > 0:
            time.sleep(delay)

        body = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def run_cmd(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a step-by-step live indexing/search demo")
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocess calls")
    parser.add_argument("--cycles", type=int, default=6, help="How many live search polls to run")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    main_py = root / "main.py"

    server = HTTPServer(("127.0.0.1", 0), DemoHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    host, port = server.server_address
    origin = f"http://{host}:{port}/"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "demo.db")

        index_cmd = [
            args.python,
            str(main_py),
            "--db",
            db_path,
            "index",
            origin,
            "2",
            "--workers",
            "2",
            "--queue-depth",
            "4",
            "--rps",
            "20",
            "--quiet",
        ]

        print("STEP 1: Start indexing in background")
        print(" ".join(index_cmd))
        index_proc = subprocess.Popen(index_cmd, cwd=str(root), text=True)

        print("STEP 2: Poll live search while indexing is active")
        for i in range(args.cycles):
            if index_proc.poll() is not None:
                break

            early = run_cmd(
                [
                    args.python,
                    str(main_py),
                    "--db",
                    db_path,
                    "search",
                    "crawler",
                    "--json",
                ],
                cwd=root,
            )

            late = run_cmd(
                [
                    args.python,
                    str(main_py),
                    "--db",
                    db_path,
                    "search",
                    "running",
                    "--json",
                ],
                cwd=root,
            )

            early_rows = json.loads(early.stdout or "[]")
            late_rows = json.loads(late.stdout or "[]")
            print(f"  poll={i + 1} crawler_hits={len(early_rows)} running_hits={len(late_rows)}")
            time.sleep(0.5)

        print("STEP 3: Wait for indexing to finish")
        exit_code = index_proc.wait(timeout=30)
        print(f"  index_exit_code={exit_code}")

        print("STEP 4: Show final status")
        status = run_cmd([args.python, str(main_py), "--db", db_path, "status"], cwd=root)
        print(status.stdout.strip())

        print("STEP 5: Show final search triples")
        search_final = run_cmd(
            [args.python, str(main_py), "--db", db_path, "search", "crawler", "--json", "--limit", "10"],
            cwd=root,
        )
        print(search_final.stdout.strip())

    server.shutdown()
    server.server_close()
    server_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
