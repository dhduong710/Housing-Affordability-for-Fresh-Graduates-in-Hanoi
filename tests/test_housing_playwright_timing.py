"""Check that the Playwright CLI records comparable timing fields."""

import contextlib
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "collection"))

import housing_crawler


class PlaywrightTimingTests(unittest.TestCase):
    def test_duration_format(self):
        self.assertEqual(housing_crawler.format_duration(3661), "1h 1m 1s")

    def test_main_records_invocation_and_total_time(self):
        with tempfile.TemporaryDirectory() as directory:
            log = {"status": "complete", "started_at_utc": datetime.now(timezone.utc).isoformat(),
                   "active_elapsed_seconds": 0.0}
            output = io.StringIO()
            with patch.object(sys, "argv", ["housing_crawler.py"]), \
                 patch.object(housing_crawler, "crawl", return_value=(Path(directory), [], log)), \
                 patch.object(housing_crawler, "write_outputs"), \
                 patch.object(housing_crawler, "save_log"), \
                 contextlib.redirect_stdout(output):
                result = housing_crawler.main()
        self.assertEqual(result, 0)
        self.assertEqual(len(log["invocations"]), 1)
        self.assertGreaterEqual(log["last_invocation_elapsed_seconds"], 0)
        self.assertEqual(log["active_elapsed_seconds"], log["last_invocation_elapsed_seconds"])
        self.assertIn("total active time:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
