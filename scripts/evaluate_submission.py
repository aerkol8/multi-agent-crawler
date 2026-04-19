from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str


def run_cmd(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def print_result(result: CheckResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name}")
    if result.details:
        print(f"       {result.details}")


class EvalHandler(BaseHTTPRequestHandler):
    routes = {
        "/": "<html><body><a href='/a'>A</a><a href='/b'>B</a>seed page</body></html>",
        "/a": "<html><body>crawler python requirement public score output</body></html>",
        "/b": "<html><body><a href='/c'>C</a>index in progress</body></html>",
        "/c": "<html><body>running while indexing</body></html>",
    }
    delays = {"/b": 1.2, "/c": 1.0}

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        payload = self.routes.get(path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return

        delay = self.delays.get(path, 0.0)
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


def required_files_check(root: Path) -> CheckResult:
    required = [
        "product_prd.md",
        "readme.md",
        "recommendation.md",
        "multi_agent_workflow.md",
        "agents/product_agent.md",
        "agents/architecture_agent.md",
        "agents/crawler_agent.md",
        "agents/search_agent.md",
        "agents/qa_agent.md",
        "agents/docs_agent.md",
        "agents/interactions_log.md",
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        return CheckResult("Required artifacts exist", False, f"missing: {', '.join(missing)}")
    return CheckResult("Required artifacts exist", True, "all required docs and agent files are present")


def compile_check(root: Path, python_bin: str) -> CheckResult:
    proc = run_cmd([python_bin, "-m", "compileall", "src", "tests", "scripts", "main.py"], cwd=root)
    if proc.returncode != 0:
        return CheckResult("Syntax compile check", False, proc.stderr.strip() or proc.stdout.strip())
    return CheckResult("Syntax compile check", True, "compileall completed successfully")


def test_check(root: Path, python_bin: str) -> CheckResult:
    proc = run_cmd([python_bin, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root)
    if proc.returncode != 0:
        tail = (proc.stdout + "\n" + proc.stderr).strip().splitlines()[-12:]
        return CheckResult("Automated tests", False, " | ".join(tail))
    return CheckResult("Automated tests", True, "unittest suite passed")


def cli_check(root: Path, python_bin: str) -> CheckResult:
    proc = run_cmd([python_bin, "main.py", "--help"], cwd=root)
    if proc.returncode != 0:
        return CheckResult("CLI availability", False, proc.stderr.strip() or "main.py --help failed")
    out = proc.stdout
    expected = ["index", "search", "status", "runs", "web"]
    missing = [token for token in expected if token not in out]
    if missing:
        return CheckResult("CLI availability", False, f"missing commands in help: {', '.join(missing)}")
    return CheckResult("CLI availability", True, "index/search/status/runs/web commands exposed")


def web_server_check(root: Path, python_bin: str) -> CheckResult:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "web-eval.db")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            host, port = sock.getsockname()

        proc = subprocess.Popen(
            [
                python_bin,
                "main.py",
                "--db",
                db_path,
                "web",
                "--host",
                host,
                "--port",
                str(port),
            ],
            cwd=str(root),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            deadline = time.time() + 8
            while time.time() < deadline:
                if proc.poll() is not None:
                    return CheckResult("Localhost web server", False, f"web command exited with code {proc.returncode}")

                try:
                    with urlopen(f"http://{host}:{port}/api/health", timeout=1.0) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                        if payload.get("ok") is True:
                            return CheckResult("Localhost web server", True, f"health endpoint reachable on {host}:{port}")
                except (URLError, json.JSONDecodeError):
                    time.sleep(0.2)

            return CheckResult("Localhost web server", False, "health endpoint did not become ready")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


def scalability_profile_check(root: Path, python_bin: str) -> CheckResult:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = str(Path(tmp) / "scalability_report.json")
        proc = run_cmd(
            [
                python_bin,
                "scripts/scalability_profile.py",
                "--pages",
                "180",
                "--workers",
                "4",
                "--queue-depth",
                "30",
                "--rps",
                "120",
                "--output",
                output_path,
            ],
            cwd=root,
            timeout=120,
        )
        if proc.returncode != 0:
            details = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            return CheckResult("Scalability profile", False, details)

        try:
            payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return CheckResult("Scalability profile", False, f"invalid profile output: {exc}")

        required_keys = {
            "completed",
            "elapsed_seconds",
            "discovered_urls",
            "urls_per_second",
            "max_snapshot_queue_depth",
            "live_search_seen_during_index",
        }
        missing = sorted(k for k in required_keys if k not in payload)
        if missing:
            return CheckResult("Scalability profile", False, f"missing keys: {', '.join(missing)}")

        if not payload.get("completed"):
            return CheckResult("Scalability profile", False, "profile crawl did not complete")

        if float(payload.get("urls_per_second", 0.0)) <= 0.0:
            return CheckResult("Scalability profile", False, "non-positive urls_per_second")

        return CheckResult(
            "Scalability profile",
            True,
            (
                f"completed with {payload.get('discovered_urls')} URLs in "
                f"{payload.get('elapsed_seconds')}s"
            ),
        )


def _parse_latest_run_id(root: Path, python_bin: str, db_path: str) -> int | None:
    proc = run_cmd([python_bin, "main.py", "--db", db_path, "runs", "--limit", "1"], cwd=root)
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not rows:
        return None
    try:
        return int(rows[0]["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _validate_search_payload(payload: str) -> tuple[bool, str, list[dict[str, Any]]]:
    try:
        rows = json.loads(payload or "[]")
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}", []

    if not isinstance(rows, list):
        return False, "search output is not a list", []

    for row in rows:
        if not isinstance(row, dict):
            return False, "search row is not an object", []
        keys = set(row.keys())
        if keys != {"word", "url", "origin", "depth", "freq", "score"}:
            return False, f"unexpected keys: {sorted(keys)}", []
    return True, "search rows use public scoring fields", rows


def _term_data_bucket(term: str) -> str:
    if not term:
        return "_"
    leading = term[0].lower()
    if leading.isdigit() or ("a" <= leading <= "z"):
        return leading
    return "_"


def _validate_term_data(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing storage directory at {path}"

    data_files = sorted(path.glob("*.data"))
    if not data_files:
        return False, "no *.data files found"

    total_lines = 0
    for data_file in data_files:
        bucket = data_file.stem
        lines = [line.strip() for line in data_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        total_lines += len(lines)

        for line in lines:
            parts = line.split(" ")
            if len(parts) < 5:
                return False, f"{data_file.name} line has fewer than 5 columns"

            try:
                float(parts[-1])
                int(parts[-2])
            except ValueError:
                return False, f"{data_file.name} trailing depth/freq columns are not numeric"

            if bucket != "all":
                expected_bucket = _term_data_bucket(parts[0])
                if bucket != expected_bucket:
                    return False, f"{data_file.name} contains term assigned to bucket {expected_bucket}"

    if total_lines <= 0:
        return False, "all *.data files are empty"

    return True, f"{len(data_files)} data files present with {total_lines} total lines"


def live_index_search_and_resume_check(root: Path, python_bin: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    server = HTTPServer(("127.0.0.1", 0), EvalHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    host, port = server.server_address
    origin = f"http://{host}:{port}/"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eval.db")

        index_cmd = [
            python_bin,
            "main.py",
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

        proc = subprocess.Popen(
            index_cmd,
            cwd=str(root),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        saw_live_result = False
        shape_ok = True
        shape_detail = ""
        score_ok = True
        score_detail = ""

        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                if proc.poll() is not None:
                    break

                search_proc = run_cmd(
                    [python_bin, "main.py", "--db", db_path, "search", "crawler", "--sort-by", "relevance", "--json"],
                    cwd=root,
                    timeout=30,
                )
                if search_proc.returncode == 0:
                    valid, detail, rows = _validate_search_payload(search_proc.stdout)
                    if not valid:
                        shape_ok = False
                        shape_detail = detail
                        break

                    if rows and proc.poll() is None:
                        saw_live_result = True
                        first = rows[0]
                        try:
                            freq = float(first["freq"])
                            depth = int(first["depth"])
                            score = float(first["score"])
                        except (KeyError, TypeError, ValueError):
                            score_ok = False
                            score_detail = "search row score fields are invalid"
                            break

                        expected = round((freq * 10.0) + 1000.0 - (depth * 5.0), 6)
                        if abs(score - expected) > 1e-6:
                            score_ok = False
                            score_detail = f"score mismatch: got {score}, expected {expected}"
                            break
                        break

                time.sleep(0.3)

            exit_code = proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            proc.kill()
            results.append(CheckResult("Live index + search", False, "indexing process timed out"))
            exit_code = -1
        finally:
            if proc.poll() is None:
                proc.kill()

        if exit_code != 0:
            results.append(CheckResult("Live index + search", False, f"index exited with code {exit_code}"))
        elif not shape_ok:
            results.append(CheckResult("Search contract", False, shape_detail))
        elif not score_ok:
            results.append(CheckResult("Public score formula", False, score_detail))
        else:
            details = "live results observed before indexing finished" if saw_live_result else "no live hit observed; run still completed"
            results.append(CheckResult("Live index + search", saw_live_result, details))
            results.append(CheckResult("Search contract", True, "search JSON rows include word/url/origin/depth/freq/score"))
            results.append(CheckResult("Public score formula", True, "score matches (freq*10)+1000-(depth*5)"))

        data_ok, data_detail = _validate_term_data(Path(db_path))
        results.append(CheckResult("term data inspectability", data_ok, data_detail))

        status_proc = run_cmd([python_bin, "main.py", "--db", db_path, "status"], cwd=root)
        if status_proc.returncode != 0:
            results.append(CheckResult("Status command", False, status_proc.stderr.strip() or "status command failed"))
        else:
            try:
                payload = json.loads(status_proc.stdout)
                runtime = payload.get("runtime") or {}
                needed = ["queue_depth", "queue_capacity", "active_workers", "throttled_events"]
                missing = [k for k in needed if k not in runtime]
                if missing:
                    results.append(CheckResult("Status telemetry", False, f"missing runtime keys: {', '.join(missing)}"))
                else:
                    results.append(CheckResult("Status telemetry", True, "runtime telemetry keys are present"))
            except json.JSONDecodeError as exc:
                results.append(CheckResult("Status telemetry", False, f"invalid JSON: {exc}"))

        # Resume flow check via forced interruption.
        interrupt_cmd = [
            python_bin,
            "main.py",
            "--db",
            db_path,
            "index",
            origin,
            "2",
            "--workers",
            "1",
            "--queue-depth",
            "2",
            "--rps",
            "20",
            "--quiet",
        ]
        interrupt_proc = subprocess.Popen(
            interrupt_cmd,
            cwd=str(root),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        interrupt_proc.terminate()
        try:
            interrupt_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            interrupt_proc.kill()
            interrupt_proc.wait(timeout=5)

        run_id = _parse_latest_run_id(root, python_bin, db_path)
        if run_id is None:
            results.append(CheckResult("Resume command", False, "could not determine latest run id for resume"))
        else:
            resume = run_cmd(
                [python_bin, "main.py", "--db", db_path, "index", "--resume-run-id", str(run_id), "--quiet"],
                cwd=root,
                timeout=60,
            )
            if resume.returncode != 0:
                details = resume.stderr.strip() or resume.stdout.strip() or f"exit code {resume.returncode}"
                results.append(CheckResult("Resume command", False, details))
            else:
                results.append(CheckResult("Resume command", True, f"run {run_id} resumed and completed"))

    server.shutdown()
    server.server_close()
    server_thread.join(timeout=2)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate submission rubric with PASS/FAIL checks")
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocess checks")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]

    checks: list[CheckResult] = []
    checks.append(required_files_check(root))
    checks.append(compile_check(root, args.python))
    checks.append(test_check(root, args.python))
    checks.append(cli_check(root, args.python))
    checks.append(web_server_check(root, args.python))
    checks.append(scalability_profile_check(root, args.python))
    checks.extend(live_index_search_and_resume_check(root, args.python))

    print("\n=== Submission Evaluation ===")
    passed_count = 0
    for item in checks:
        print_result(item)
        if item.passed:
            passed_count += 1

    total = len(checks)
    print(f"\nSummary: {passed_count}/{total} checks passed")

    if passed_count == total:
        print("Overall: PASS")
        return 0

    print("Overall: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
