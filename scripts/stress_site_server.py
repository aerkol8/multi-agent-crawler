from __future__ import annotations

import argparse
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def parse_page(path: str) -> int | None:
    if not path.startswith("/p"):
        return None
    try:
        return int(path[2:])
    except ValueError:
        return None


class StressHandler(BaseHTTPRequestHandler):
    pages = 2000
    root_links = 300
    fanout = 30
    delay_ms = 0.0
    jitter_ms = 0.0
    payload_size = 120

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        self._delay_if_needed()

        if path == "/":
            links = "".join(f"<a href='/p{i}'>seed-{i}</a>" for i in range(self.root_links))
            payload = "seed " * self.payload_size
            body = f"<html><body><h1>stress-root</h1><p>{payload}</p>{links}</body></html>"
            return self._send_html(body)

        idx = parse_page(path)
        if idx is None or idx < 0 or idx >= self.pages:
            self.send_response(404)
            self.end_headers()
            return

        links = []
        for step in range(1, self.fanout + 1):
            nxt = (idx + step) % self.pages
            links.append(f"<a href='/p{nxt}'>n{nxt}</a>")

        payload = f"page {idx} crawler stress signal " + ("data " * self.payload_size)
        body = (
            "<html><head><title>stress-page</title></head><body>"
            f"<h2>node-{idx}</h2><p>{payload}</p>{''.join(links)}"
            "</body></html>"
        )
        self._send_html(body)

    def _delay_if_needed(self) -> None:
        delay = self.delay_ms
        if self.jitter_ms > 0:
            delay += random.uniform(0.0, self.jitter_ms)
        if delay > 0:
            time.sleep(delay / 1000.0)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local high-link-density site for crawler stress tests")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=9001, help="Bind port")
    parser.add_argument("--pages", type=int, default=2000, help="Total synthetic pages")
    parser.add_argument("--root-links", type=int, default=300, help="How many links the root page exposes")
    parser.add_argument("--fanout", type=int, default=30, help="Outgoing links per non-root page")
    parser.add_argument("--delay-ms", type=float, default=0.0, help="Base response delay per request in ms")
    parser.add_argument("--jitter-ms", type=float, default=0.0, help="Extra random response delay in ms")
    parser.add_argument("--payload-size", type=int, default=120, help="Token payload multiplier per page")
    args = parser.parse_args()

    if args.pages < 10:
        raise SystemExit("--pages must be at least 10")
    if args.root_links < 1:
        raise SystemExit("--root-links must be >= 1")
    if args.fanout < 1:
        raise SystemExit("--fanout must be >= 1")

    StressHandler.pages = args.pages
    StressHandler.root_links = min(args.root_links, args.pages)
    StressHandler.fanout = min(args.fanout, args.pages - 1)
    StressHandler.delay_ms = max(0.0, args.delay_ms)
    StressHandler.jitter_ms = max(0.0, args.jitter_ms)
    StressHandler.payload_size = max(20, args.payload_size)

    server = ThreadingHTTPServer((args.host, args.port), StressHandler)
    host, port = server.server_address
    print("stress site running")
    print(f"origin: http://{host}:{port}/")
    print(
        "config:",
        f"pages={StressHandler.pages}",
        f"root_links={StressHandler.root_links}",
        f"fanout={StressHandler.fanout}",
        f"delay_ms={StressHandler.delay_ms}",
        f"jitter_ms={StressHandler.jitter_ms}",
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
