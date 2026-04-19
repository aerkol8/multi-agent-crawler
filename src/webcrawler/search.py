from __future__ import annotations

from .models import SearchHit
from .storage import Storage
from .utils import tokenize


class SearchService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def search(self, query: str, limit: int = 20, sort_by: str = "relevance") -> list[SearchHit]:
        return self.storage.search(tokenize(query), limit=limit, sort_by=sort_by)
