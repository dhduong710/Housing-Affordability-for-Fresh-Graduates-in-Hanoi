"""Exercise automatic restarts with real checkpoints and fake HTTP/browser/time."""

import csv
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from collection.jobs.listing_client import SourceStopped
from collection.jobs.resume_controller import run_auto_collection

URL = "https://www.topcv.vn/tim-viec-lam-moi-nhat?page=1"


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class AutoResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = Clock()
        self.calls, self.closed, self.launched = [], [], []
        self.blocks = {}
        self.args = SimpleNamespace(
            resume=None,
            max_pages=20,
            max_jobs=None,
            channel="chrome",
            headless=True,
            delay=15,
            jitter=5,
            max_retries=3,
            backoff=30,
            max_auto_wait=240,
            timeout=60,
            resume_cooldown=60,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def browser(self, state, *args, **kwargs):
        owner = self
        session = len(state.manifest["sessions"])
        owner.launched.append((session, owner.clock()))

        class Browser:
            def load(self, task):
                page = task["page_number"]
                owner.calls.append((session, page))
                record = {
                    "url": task["url"],
                    "at": owner.clock().isoformat(),
                    "status": 200,
                    "session": session,
                    "response_headers": {},
                }
                state.manifest["requests"].append(record)
                block = owner.blocks.get(session)
                if block and block[0] == page:
                    record["status"] = block[1]
                    if len(block) > 2:
                        record["response_headers"]["retry-after"] = str(block[2])
                    if block[1] == 403:
                        raise SourceStopped("access_blocked: HTTP 403")
                    if block[1] == 401:
                        raise SourceStopped("access_blocked: HTTP 401")
                    state.manifest["not_before"] = (
                        owner.clock() + timedelta(seconds=180)
                    ).isoformat()
                    raise SourceStopped("retry_limit: resume later")
                html = f'<div class="job-list-search-result"><div class="job-item-search-result"><h3 class="title"><a href="/viec-lam/test/{page}.html">Job {page}</a></h3></div></div><span id="job-listing-paginate-text">{page} / 20 trang</span>'
                if page < 20:
                    html += f'<a rel="next" data-href="?page={page + 1}">Next</a>'
                return html, owner.clock().isoformat()

            def close(self):
                owner.closed.append(session)

        return Browser()

    def run_auto(self, sleep=None):
        with patch(
            "collection.jobs.collection_runner.ListingClient", side_effect=self.browser
        ):
            return run_auto_collection(
                self.args, self.root, clock=self.clock, sleep=sleep or self.clock.sleep
            )

    def checkpoint(self):
        path = next((self.root / "data/raw/jobs").glob("*/checkpoint.json"))
        return path.parent, json.loads(path.read_bytes())

    def test_block_then_resume_to_twenty_in_same_csv_without_repeating_completed_pages(
        self,
    ):
        self.blocks = {1: (3, 403), 2: (7, 503)}
        self.assertEqual(self.run_auto(), 0)
        folder, data = self.checkpoint()
        with (folder / "jobs_topcv.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(
            [row["job_id"] for row in rows], [str(n) for n in range(1, 21)]
        )
        self.assertEqual(
            [page for session, page in self.calls if session == 2], [3, 4, 5, 6, 7]
        )
        self.assertEqual(
            [page for session, page in self.calls if session == 3], list(range(7, 21))
        )
        self.assertEqual(data["manifest"]["auto_resume"]["resumes_used"], 2)
        self.assertEqual(data["manifest"]["auto_resume"]["status"], "source_exhausted")
        self.assertEqual(self.closed, [1, 2, 3])
        self.assertGreaterEqual(
            sum(self.clock.sleeps), 240
        )  # 60s then server-directed 180s.
        self.assertEqual(len(list((self.root / "data/raw/jobs").iterdir())), 1)

    def test_unlimited_restarts_pass_five_and_reach_twenty(self):
        self.args.resume_cooldown = 120
        self.blocks = {session: (session + 1, 403) for session in range(1, 8)}
        self.assertEqual(self.run_auto(), 0)
        _, data = self.checkpoint()
        self.assertEqual(len(data["rows"]), 20)
        self.assertEqual(data["manifest"]["auto_resume"]["resumes_used"], 7)
        self.assertEqual(sum(self.clock.sleeps), 7 * 120)
        self.assertEqual(
            [
                int((later[1] - earlier[1]).total_seconds())
                for earlier, later in zip(self.launched, self.launched[1:])
            ],
            [120] * 7,
        )
        self.assertEqual(data["manifest"]["auto_resume"]["cooldown_mode"], "fixed")
        self.assertEqual(
            [page for session, page in self.calls if session == 8], list(range(8, 21))
        )

    def test_server_retry_after_is_not_shortened(self):
        self.blocks = {1: (1, 403, 300)}
        self.assertEqual(self.run_auto(), 0)
        self.assertEqual(sum(self.clock.sleeps), 300)
        self.assertGreaterEqual(
            (self.launched[1][1] - self.launched[0][1]).total_seconds(), 300
        )

    def test_ctrl_c_during_cooldown_preserves_deadline_and_restart_count(self):
        self.blocks = {1: (3, 403)}

        def interrupt(seconds):
            _, data = self.checkpoint()
            self.assertIsNotNone(data["manifest"]["auto_resume"]["next_resume_at"])
            self.assertEqual(self.closed, [1])  # No browser left open during cooldown.
            raise KeyboardInterrupt()

        self.assertEqual(self.run_auto(interrupt), 130)
        folder, before = self.checkpoint()
        deadline = before["manifest"]["auto_resume"]["next_resume_at"]
        self.args.resume = folder
        self.assertEqual(self.run_auto(), 0)
        _, after = self.checkpoint()
        self.assertEqual(after["manifest"]["auto_resume"]["resumes_used"], 1)
        self.assertEqual(self.launched[1][1].isoformat(), deadline)
        self.assertEqual(self.calls[3], (2, 3))

    def test_401_is_not_retried_even_with_retry_after(self):
        self.blocks = {1: (1, 401, 300)}
        self.assertEqual(self.run_auto(), 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(len(self.launched), 1)

    def test_page_target_reached_stops_before_requesting_page_twenty_one(self):
        self.args.max_pages = 2
        self.assertEqual(self.run_auto(), 0)
        folder, data = self.checkpoint()
        self.assertEqual(self.calls, [(1, 1), (1, 2)])
        self.assertEqual(data["manifest"]["auto_resume"]["status"], "target_reached")
        self.args.resume = folder
        self.assertEqual(self.run_auto(), 0)
        self.assertEqual(len(self.launched), 1)

    def test_data_error_does_not_trigger_restart(self):
        def fail(state, client, *args):
            raise ValueError("Saved page payload checksum mismatch")

        with patch("collection.jobs.collection_runner.collect", side_effect=fail):
            self.assertEqual(self.run_auto(), 1)
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.clock.sleeps, [])
