from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from webcrawler.utils import normalize_url, term_frequencies, tokenize


class UtilsTestCase(unittest.TestCase):
    def test_normalize_url_removes_fragment_and_sorts_query(self) -> None:
        url = "HTTPS://Example.com:443/path/../a?b=2&a=1#frag"
        self.assertEqual(normalize_url(url), "https://example.com/a?a=1&b=2")

    def test_tokenize_and_term_frequencies(self) -> None:
        tokens = tokenize("Crawler crawler index search")
        freqs = term_frequencies(tokens)
        self.assertEqual(freqs["crawler"], 0.5)
        self.assertEqual(freqs["index"], 0.25)
        self.assertEqual(freqs["search"], 0.25)


if __name__ == "__main__":
    unittest.main()
