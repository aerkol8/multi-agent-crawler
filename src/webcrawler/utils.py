from __future__ import annotations

import posixpath
import re
from collections import Counter
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")


class LinkAndTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: set[str] = set()
        self.text_parts: list[str] = []
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.add(href)
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if not data or data.isspace():
            return
        stripped = data.strip()
        if not stripped:
            return
        self.text_parts.append(stripped)
        if self._in_title:
            if self.title:
                self.title += " "
            self.title += stripped


def normalize_url(candidate: str, base_url: str | None = None) -> str | None:
    if not candidate:
        return None

    absolute = urljoin(base_url, candidate) if base_url else candidate
    parsed = urlparse(absolute)

    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None

    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{ascii_hostname}:{port}"
    else:
        netloc = ascii_hostname

    path = parsed.path or "/"
    normalized_path = posixpath.normpath(path)
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if path.endswith("/") and not normalized_path.endswith("/"):
        normalized_path += "/"

    # Keep reserved URL path separators while percent-encoding non-ASCII bytes.
    encoded_path = quote(normalized_path, safe="/%-._~!$&'()*+,;=:@")

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_items.sort()
    normalized_query = urlencode(query_items, doseq=True)

    normalized = parsed._replace(
        scheme=scheme,
        netloc=netloc,
        path=encoded_path,
        params="",
        query=normalized_query,
        fragment="",
    )
    return urlunparse(normalized)


def extract_links_and_text(html: str, base_url: str) -> tuple[set[str], str, str]:
    parser = LinkAndTextExtractor()
    parser.feed(html)
    parser.close()

    normalized_links: set[str] = set()
    for raw_link in parser.links:
        normalized = normalize_url(raw_link, base_url=base_url)
        if normalized:
            normalized_links.add(normalized)

    text = " ".join(parser.text_parts)
    return normalized_links, text, parser.title


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def term_frequencies(tokens: Iterable[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {term: count / total for term, count in counts.items()}
