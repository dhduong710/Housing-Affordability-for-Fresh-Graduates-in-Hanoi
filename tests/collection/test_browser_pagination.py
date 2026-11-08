"""Opt-in Chrome regression checks using routed fixtures, never the real website."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from playwright.sync_api import sync_playwright

from collection.jobs.cdp_browser import CdpBrowser
from collection.jobs.listing_client import ListingClient, SourceStopped
from collection.jobs.utils.collection_state import CollectionState

URL = "https://www.topcv.vn/tim-viec-lam-moi-nhat"


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_TESTS") == "1",
    "Set RUN_BROWSER_TESTS=1; requires Chrome",
)
class NativeAjaxTests(unittest.TestCase):
    def exercise(
        self, status=200, duplicate=False, replace_root=False, root_target=False
    ):
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as runtime:
            root = Path(temporary)
            state = CollectionState.create(root, [{"name": "all_jobs", "url": URL}], {})
            client = ListingClient(state, "chrome", True, 3, jitter=0)
            client.cdp_browser = CdpBrowser(runtime, "chrome", True, 10000)
            client.browser = client.cdp_browser.start()
            client.context = client.browser.contexts[0]
            client.page = client.context.new_page()
            client.timeout_ms = 5000
            client.ua = "fixture"
            client.pacer.wait = lambda: 0

            class Allow:
                def can_fetch(self, *args):
                    return True

            client.robots = Allow()
            target = (
                URL
                + "?page=2&type_keyword=1&disable_auto_detect_type_keyword=1&saturday_status=0&u_sr_id=test"
            )
            if root_target:
                target = "/?page=2&u_sr_id=test"

            def cards(number):
                return f'<div class="job-item-search-result"><h3 class="title"><a href="/viec-lam/test/{number}.html">Job {number}</a></h3></div>'

            original = cards(1)
            following = original if duplicate else cards(2)
            update = (
                "document.querySelector('.job-list-search-result').outerHTML = '<div class=\"job-list-search-result\">' + data.html + '</div>';"
                if replace_root
                else "document.querySelector('.job-list-search-result').innerHTML = data.html;"
            )
            html = f'''<title>Fixture jobs</title>
                <div class="job-list-search-result">{original}</div>
                <span id="job-listing-paginate-text">1 / 2 trang</span>
                <a rel="next" data-href="{target}">Next</a>
                <script>
                document.querySelector('[rel="next"]').onclick = async function(event) {{
                    event.preventDefault();
                    fetch('/v4/image/survey/search-job/animation/bad.json');
                    const target = new URL(this.dataset.href, location.href);
                    target.pathname = location.pathname;
                    const response = await fetch(target.href, {{method:'POST'}});
                    if (!response.ok) return;
                    const data = await response.json();
                    history.replaceState(null, '', target.href);
                    document.querySelector('#job-listing-paginate-text').textContent = '2 / 2 trang';
                    setTimeout(() => {{{update}}}, 200);
                }};
                </script>'''

            def route(request_route):
                request = request_route.request
                if "/animation/" in request.url:
                    request_route.fulfill(status=403, body="Not a listing response")
                elif request.resource_type == "document":
                    request_route.fulfill(
                        status=200, content_type="text/html", body=html
                    )
                else:
                    request_route.fulfill(
                        status=status,
                        content_type="application/json",
                        body=json.dumps({"html": following}),
                    )

            client.page.route("**/*", route)
            try:
                client.load({"url": URL + "?page=1", "page_number": 1})
                if status == 403:
                    with self.assertRaisesRegex(SourceStopped, "HTTP 403"):
                        client.load({"url": URL + "?page=2", "page_number": 2})
                else:
                    updated, _ = client.load(
                        {"url": URL + "?page=2", "page_number": 2}
                    )
                    self.assertIn("Job 1" if duplicate else "Job 2", updated)
                    if not duplicate:
                        self.assertNotIn("Job 1", updated)
                self.assertIsNone(
                    client.page.locator('[rel="next"]').get_attribute("href")
                )
                self.assertEqual(
                    state.manifest["requests"][-1]["response_type"], "fetch"
                )
                self.assertEqual(state.manifest["requests"][-1]["status"], status)
            finally:
                client.close()

    def test_native_ajax_pagination_updates_listing_and_keeps_handler(self):
        for options in [
            {},
            {"duplicate": True},
            {"replace_root": True},
            {"root_target": True},
        ]:
            with self.subTest(options=options):
                self.exercise(**options)

    def test_listing_403_is_reported_without_waiting_for_dom(self):
        self.exercise(status=403)
