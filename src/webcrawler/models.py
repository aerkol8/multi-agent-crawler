from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrawlTask:
    url: str
    depth: int


@dataclass(frozen=True)
class SearchTriple:
    relevant_url: str
    origin_url: str
    depth: int
