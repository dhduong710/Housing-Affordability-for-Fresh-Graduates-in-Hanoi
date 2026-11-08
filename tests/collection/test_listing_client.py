"""Rate-limit and browser integration checks without network access or real sleeps."""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from playwright.sync_api import TimeoutError as BrowserTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from collection.jobs.collection_runner import run_search_collection
from collection.jobs.listing_client import ListingClient, SourceStopped
from collection.jobs.utils.collection_state import CollectionState


URL = "https://www.topcv.vn/tim-viec-lam-moi-nhat"
HTML = '<div class="job-list-search-result"><div class="job-item-search-result"><h3 class="title"><a href="/viec-lam/dev/1.html">Job</a></h3></div></div>'


class BrowserPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = CollectionState.create(
            self.root, [{"name": "all_jobs", "url": URL}], {}
        )
        self.client = ListingClient(self.state, "chrome", True, 8, jitter=0)
        self.client.browser = True
        self.client.page = MagicMock()
        self.client.page.title.return_value = "Jobs"
        self.client.page.locator.return_value.all.return_value = []
        self.client.page.locator.return_value.count.return_value = 0
        self.client.page.url = URL
        self.client.robots = MagicMock()
        self.client.ua = "fixture"
        self.client.pacer.wait = MagicMock(return_value=0)
        self.client.pacer.sleep = MagicMock()
        self.client._navigate = MagicMock()

        self.client.page.content.return_value = HTML

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def response(status, header=""):
        response = MagicMock()
        response.status = status
        response.url = URL
        response.request.resource_type = "document"
        response.request.all_headers.return_value = {}
        response.headers = {"retry-after": header}
        return response

    def test_429_then_success_checkpoints_cooldown_before_retry(self):
        self.client._navigate.side_effect = [
            self.response(429, "45"),
            self.response(200),
        ]

        def check_checkpoint(seconds):
            checkpoint = json.loads((self.state.folder / "checkpoint.json").read_text())
            deadline = datetime.fromisoformat(checkpoint["manifest"]["not_before"])
            self.assertGreater(deadline, datetime.now(UTC) + timedelta(seconds=40))
            self.assertEqual(seconds, 45)

        self.client.pacer.sleep.side_effect = check_checkpoint
        self.assertEqual(self.client.load(self.state.next_task(None))[0], HTML)
        self.assertEqual(
            [r["status"] for r in self.state.manifest["requests"]], [429, 200]
        )
        self.assertEqual(self.state.manifest["rate_delay"], 16)
        self.assertNotIn("not_before", self.state.manifest)

    def test_long_cooldown_is_saved_without_retry_or_sleep(self):
        self.client._navigate.return_value = self.response(429, "3600")
        with self.assertRaisesRegex(SourceStopped, "cooldown_too_long"):
            self.client.load(self.state.next_task(None))
        self.assertEqual(self.client._navigate.call_count, 1)
        self.client.pacer.sleep.assert_not_called()
        resumed = CollectionState.resume(self.root, self.state.folder)
        self.assertEqual(resumed.queue[0]["url"], URL)
        self.assertIn("not_before", resumed.manifest)
        with patch("collection.jobs.collection_runner.ListingClient") as browser:
            with self.assertRaisesRegex(ValueError, "not elapsed"):
                run_search_collection(
                    SimpleNamespace(resume=self.state.folder), self.root
                )
            browser.assert_not_called()

    def test_navigation_timeouts_retry_with_short_backoff_then_return_listing(self):
        self.client._navigate.side_effect = [
            BrowserTimeout('first navigation timeout'),
            BrowserTimeout('second navigation timeout'),
            self.response(200),
        ]
        self.assertEqual(self.client.load(self.state.next_task(None))[0], HTML)
        self.assertEqual(self.client._navigate.call_count, 3)
        self.assertEqual([call.args[0] for call in self.client.pacer.sleep.call_args_list],
                         [15, 30])
        self.assertEqual([record.get('error') for record in self.state.manifest['requests']],
                         ['navigation_timeout', 'navigation_timeout', None])
        self.client.page.wait_for_load_state.assert_called_once_with(
            'domcontentloaded', timeout=10000)
        self.assertNotIn('not_before', self.state.manifest)

    def configure_next(self, target=None, status=200):
        target = target or URL + "?page=2"
        self.client.loaded_list_url = URL
        self.client.loaded_list_number = 1
        self.client._navigate = ListingClient._navigate.__get__(self.client)
        controls = self.client.page.locator.return_value
        controls.count.return_value = 1
        link = controls.nth.return_value
        link.is_visible.return_value = True
        link.get_attribute.side_effect = lambda name: (
            target if name == "data-href" else None
        )
        link.evaluate.return_value = False
        link.click.side_effect = lambda **kwargs: setattr(
            self.client.page, "url", target
        )
        response = self.response(status)
        response.url = target
        response.request.resource_type = "document"
        response.request.frame = self.client.page.main_frame
        response.request.all_headers.return_value = {
            "referer": URL,
            "cookie": "private",
            "authorization": "private",
        }
        self.client.page.expect_response.return_value.__enter__.return_value.value = (
            response
        )
        return link, response

    def test_successful_next_is_clicked_and_logged_with_filtered_request_headers(self):
        link, response = self.configure_next()
        task = {"url": URL + "?page=2", "page_number": 2}
        self.assertEqual(self.client.load(task)[0], HTML)
        link.click.assert_called_once_with(timeout=30000)
        link.scroll_into_view_if_needed.assert_called_once_with(timeout=30000)
        link.evaluate.assert_called_once()  # Disabled check only; no href mutation.
        self.assertNotIn("setAttribute", link.evaluate.call_args.args[0])
        self.client.page.goto.assert_not_called()
        self.assertEqual(self.client.loaded_list_number, 2)
        record = self.state.manifest["requests"][-1]
        self.assertEqual(record["navigation_mode"], "click_next")
        self.assertEqual(record["clicked_next_url"], task["url"])
        self.assertEqual(record["request_headers"], {"referer": URL})
        predicate = self.client.page.expect_response.call_args.args[0]
        self.assertTrue(predicate(response))
        response.request.resource_type = "xhr"
        response.url = (
            URL
            + "?page=2&type_keyword=1&disable_auto_detect_type_keyword=1&saturday_status=0&u_sr_id=tracking"
        )
        self.assertTrue(predicate(response))
        response.url = URL + "?page=3"
        self.assertFalse(predicate(response))
        response.url = (
            "https://www.topcv.vn/v4/image/survey/search-job/animation/bad.json"
        )
        self.assertFalse(predicate(response))
        self.assertEqual(
            self.client.page.wait_for_function.call_args.kwargs["arg"],
            {"number": 2, "requirePage": True},
        )

    def test_wrong_filter_next_is_rejected_without_fallback_to_goto(self):
        link, _ = self.configure_next(URL + "-tai-ha-noi-l1?page=2")
        with self.assertRaisesRegex(SourceStopped, "No enabled Next"):
            self.client._navigate(URL + "?page=2")
        link.click.assert_not_called()
        self.client.page.goto.assert_not_called()

    def test_403_after_next_click_does_not_retry_or_fall_back(self):
        link, response = self.configure_next(status=403)
        response.headers = {"cf-ray": "fixture-ray", "cf-mitigated": "challenge"}
        with self.assertRaisesRegex(SourceStopped, "HTTP 403"):
            self.client.load({"url": URL + "?page=2", "page_number": 2})
        link.click.assert_called_once()
        self.client.page.goto.assert_not_called()
        self.client.page.reload.assert_not_called()
        self.client.pacer.sleep.assert_not_called()
        record = self.state.manifest["requests"][-1]
        self.assertEqual(record["request_headers"], {"referer": URL})
        self.assertEqual(
            record["access_diagnostic"],
            {
                "via_cloudflare": True,
                "challenge_response": True,
                "navigation_mode": "click_next",
                "headless": True,
            },
        )

    def test_retry_after_5xx_reloads_target_document_instead_of_clicking_twice(self):
        link, _ = self.configure_next(status=503)
        response = self.response(200)
        response.url = URL + "?page=2"
        self.client.page.reload.return_value = response
        self.assertEqual(
            self.client.load({"url": URL + "?page=2", "page_number": 2})[0], HTML
        )
        link.click.assert_called_once()
        self.client.page.reload.assert_called_once_with(
            wait_until="commit", timeout=30000
        )
        self.client.page.goto.assert_not_called()
        self.assertEqual(
            self.state.manifest["requests"][-1]["navigation_mode"], "reload_list_retry"
        )

    def test_server_error_removing_next_preserves_pending_page_for_session_restart(
        self,
    ):
        link, _ = self.configure_next(status=500)

        def server_error_removes_controls(**kwargs):
            self.client.page.locator.return_value.count.return_value = 0

        link.click.side_effect = server_error_removes_controls
        with self.assertRaisesRegex(SourceStopped, "retry_limit: Next unavailable"):
            self.client.load({"url": URL + "?page=2", "page_number": 2})
        self.assertEqual(self.state.manifest["requests"][-1]["status"], 500)
        link.click.assert_called_once()
        self.client.pacer.sleep.assert_called_once_with(15)
        self.client.page.goto.assert_not_called()
        self.client.page.reload.assert_not_called()

    def test_robots_are_checked_in_memory_each_session_without_files_or_logs(self):
        allow = "User-agent: *\nAllow: /\n"
        deny = "User-agent: *\nDisallow: /tim-viec-lam-moi-nhat\nCrawl-delay: 12\n"
        with (
            patch("collection.jobs.listing_client.sync_playwright"),
            patch("collection.jobs.listing_client.CdpBrowser") as factory,
        ):
            context = factory.return_value.start.return_value.contexts[0]
            context.new_page.return_value.evaluate.return_value = "fixture"
            response = context.request.get.return_value
            response.status = 200
            response.text.side_effect = [allow, deny]
            self.client.start()
            self.assertTrue(self.client.robots.can_fetch("fixture", URL))
            self.client.close()
            self.client.start()
            self.assertFalse(self.client.robots.can_fetch("fixture", URL))
            self.assertEqual(self.client.pacer.delay, 12)
            self.assertEqual(context.request.get.call_count, 2)
            self.assertEqual(self.state.manifest["requests"], [])
            self.assertEqual(list(self.state.folder.glob("robots*.txt")), [])
            self.client.close()

    def test_unavailable_robots_stops_without_saving_response(self):
        with (
            patch("collection.jobs.listing_client.sync_playwright"),
            patch("collection.jobs.listing_client.CdpBrowser") as factory,
        ):
            context = factory.return_value.start.return_value.contexts[0]
            context.new_page.return_value.evaluate.return_value = "fixture"
            context.request.get.return_value.status = 403
            with self.assertRaisesRegex(SourceStopped, "robots unavailable: HTTP 403"):
                self.client.start()
            context.request.get.return_value.text.assert_not_called()
            self.assertEqual(list(self.state.folder.glob("robots*.txt")), [])
            self.assertEqual(self.state.manifest["requests"], [])
            self.client.close()
