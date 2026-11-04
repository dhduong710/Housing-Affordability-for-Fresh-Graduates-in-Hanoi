"""Focused checks for access blocks and transient server responses."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "collection"))

from housing_requests_crawler import AccessBlocked, fetch_html


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        return next(self.responses)


class FetchHtmlTests(unittest.TestCase):
    def test_challenge_page_with_http_200_is_blocked(self):
        session = FakeSession([FakeResponse(200, "<title>Just a moment...</title>")])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AccessBlocked):
                fetch_html(session, "https://example.test/p1", 10, 2, Path(directory), {"errors": []})
        self.assertEqual(session.calls, 1)

    def test_403_is_not_retried(self):
        session = FakeSession([FakeResponse(403, "blocked")])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AccessBlocked):
                fetch_html(session, "https://example.test/p1", 10, 2, Path(directory), {"errors": []})
        self.assertEqual(session.calls, 1)

    def test_transient_503_is_retried(self):
        session = FakeSession([FakeResponse(503, "unavailable"), FakeResponse(200, "<html>ok</html>")])
        log = {"errors": []}
        with tempfile.TemporaryDirectory() as directory, patch("housing_requests_crawler.time.sleep") as sleep:
            html, status, _ = fetch_html(session, "https://example.test/p1", 10, 2, Path(directory), log)
        self.assertEqual((html, status, session.calls), ("<html>ok</html>", 200, 2))
        self.assertEqual(len(log["errors"]), 1)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
