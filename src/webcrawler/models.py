from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrawlTask:
    url: str
    depth: int


@dataclass(frozen=True)
class SearchHit:
    word: str
    url: str
    origin: str
    depth: int
    freq: float
    score: float
