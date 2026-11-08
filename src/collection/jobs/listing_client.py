"""Sequential browser collection with polite pacing and bounded server backoff."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import sync_playwright

from collection.jobs.cdp_browser import CdpBrowser
from collection.jobs.sources.pagination import (
    list_position,
    page_url,
    same_listing_page,
)
from collection.jobs.utils.collection_state import utc_now
from collection.jobs.utils.rate_limit import RequestPacer


class SourceStopped(RuntimeError):
    pass


class ListingClient:
    def __init__(self, state, channel, headless, delay, jitter=5, max_retries=2,
                 backoff=15, max_auto_wait=60, timeout=30):
        self.state, self.channel, self.headless = state, channel, headless
        self.pacer = RequestPacer(max(delay, state.manifest.get('rate_delay', 0)), jitter, backoff)
        self.max_retries, self.max_auto_wait = max_retries, max_auto_wait
        self.timeout_ms = timeout * 1000
        self.runtime = self.browser = None
        self.cdp_browser = None
        self.navigation_mode = 'goto'
        self.loaded_list_url = None
        self.loaded_list_number = None
        self.clicked_next_url = None
        self.retrying_navigation = False

    def close(self):
        try:
            if self.cdp_browser:
                self.cdp_browser.close()
        finally:
            if self.runtime:
                self.runtime.stop()

    def _record(self, url, status=None, **extra):
        item = {'url': url, 'status': status, 'at': utc_now(),
                'session': len(self.state.manifest['sessions']), **extra}
        self.state.manifest['requests'].append(item)
        return item

    def start(self):
        self.runtime = sync_playwright().start()
        self.cdp_browser = CdpBrowser(self.runtime, self.channel, self.headless, self.timeout_ms)
        self.browser = self.cdp_browser.start()
        self.context = self.browser.contexts[0]
        self.page = self.context.new_page()
        self.ua = self.page.evaluate('navigator.userAgent')
        self.state.manifest['user_agent'] = self.ua
        self.state.manifest.setdefault('browser_sessions', []).append({
            'session': len(self.state.manifest['sessions']), 'version': self.browser.version,
            'channel': self.channel, 'user_agent': self.ua, 'launch_mode': 'cdp',
            'headless': self.headless, 'webdriver': self.page.evaluate('navigator.webdriver')})
        url = 'https://www.topcv.vn/robots.txt'
        response = self.context.request.get(url, timeout=self.timeout_ms)
        self.pacer.finished()
        if response.status != 200:
            raise SourceStopped(f'robots unavailable: HTTP {response.status}')
        text = response.text()
        self.robots = RobotFileParser(url)
        self.robots.parse(text.splitlines())
        self.pacer.delay = max(self.pacer.delay, self.robots.crawl_delay(self.ua) or 0)
        rate = self.robots.request_rate(self.ua)
        if rate:
            self.pacer.delay = max(self.pacer.delay, rate.seconds / rate.requests)
        self.state.manifest['rate_delay'] = self.pacer.delay

    def _blocked(self):
        title = self.page.title().lower()
        if any(value in title for value in ['just a moment', 'attention required', 'access denied', 'captcha', 'verify you are human']):
            return True
        return any(element.is_visible() for element in self.page.locator(
            'iframe[src*="captcha"], iframe[src*="challenges.cloudflare.com"], #challenge-running, #challenge-form').all())

    def _navigate(self, url):
        self.clicked_next_url = None
        if self.loaded_list_url is None:
            self.navigation_mode = 'goto_list'  # Initial page or checkpoint restoration only.
            return self.page.goto(url, wait_until='commit', timeout=self.timeout_ms)
        expected = page_url(self.loaded_list_url, self.loaded_list_number + 1)
        if not same_listing_page(url, expected):
            raise SourceStopped('Requested listing is not the next page in the current scope')
        if self.retrying_navigation and same_listing_page(self.page.url, url):
            # A timeout/5xx may have already navigated to the target. Retry that document.
            self.navigation_mode = 'reload_list_retry'
            return self.page.reload(wait_until='commit', timeout=self.timeout_ms)
        if not same_listing_page(self.page.url, self.loaded_list_url):
            raise SourceStopped('Browser is no longer on the expected listing before Next click')
        self.navigation_mode = 'click_next'
        controls = self.page.locator('a[rel~="next"][data-href], a[rel~="next"][href]')
        observed_controls = []
        for index in range(controls.count()):
            link = controls.nth(index)
            if not link.is_visible():
                continue
            native_href = link.get_attribute('href')
            target = link.get_attribute('data-href') or native_href
            if len(observed_controls) < 5:
                observed_controls.append({'href': native_href, 'target': target})
            if native_href and not same_listing_page(urljoin(self.page.url, native_href), url):
                continue
            target_url = urljoin(self.page.url, target) if target else None
            # AJAX-rendered pagination may use /?page=N as a page token.
            # Only data-href without a native href can use this shorthand;
            # the response must still come from the current listing route.
            expected_target = (urljoin(self.loaded_list_url, '?' + urlsplit(target).query)
                               if not native_href and target and target.startswith('/?')
                               else target_url)
            if not expected_target or not same_listing_page(expected_target, url):
                continue
            disabled = link.evaluate("element => element.getAttribute('aria-disabled') === 'true' || !!element.closest('.disabled, [aria-disabled=\"true\"]')")
            if disabled:
                continue
            self.clicked_next_url = target_url
            link.scroll_into_view_if_needed(timeout=self.timeout_ms)
            self.page.evaluate("""() => {
                window.__topcvListingUpdate?.observer?.disconnect();
                const root = document.querySelector('.job-list-search-result');
                const state = {changed: false, root};
                if (root) {
                    state.observer = new MutationObserver(() => { state.changed = true; });
                    state.observer.observe(root, {childList: true, subtree: true});
                }
                window.__topcvListingUpdate = state;
            }""")
            # Match the actual listing request, including native POST/XHR updates.
            # Do not rewrite the anchor: the site's click handler owns navigation.
            with self.page.expect_response(lambda response:
                    response.request.resource_type in {'document', 'xhr', 'fetch'}
                    and response.request.frame == self.page.main_frame
                    and same_listing_page(response.url, url)
                    and not 300 <= response.status < 400, timeout=self.timeout_ms) as pending:
                link.click(timeout=self.timeout_ms)
            return pending.value
        if self.retrying_navigation and not observed_controls:
            raise SourceStopped('retry_limit: Next unavailable after transient failure; pending page retained for a new session')
        raise SourceStopped('No enabled Next control points to the expected page; checkpoint retained; '
                            + json.dumps(observed_controls, ensure_ascii=True))

    def _retry(self, record, attempt, header=''):
        now = datetime.now(timezone.utc)
        seconds = self.pacer.retry_delay(attempt, header, now)
        try:
            until = now + timedelta(seconds=seconds)
        except OverflowError:
            until = datetime.max.replace(tzinfo=timezone.utc)
        self.state.manifest['not_before'] = until.isoformat()
        self.state.manifest['rate_delay'] = self.pacer.delay
        record.update(retry_after=header, cooldown_seconds=seconds,
                      retry_not_before=until.isoformat())
        reason = 'retry_limit' if attempt > self.max_retries else 'cooldown_too_long' if seconds > self.max_auto_wait else None
        record['automatic_retry'] = reason is None
        # Persist cooldown before sleeping, including if interrupted during the wait.
        self.state.save()
        print(json.dumps({'event': 'cooldown', 'url': record['url'], 'seconds': seconds,
                          'automatic_retry': reason is None, 'reason': reason}), flush=True)
        if reason:
            raise SourceStopped(f'{reason}: resume no earlier than {until.isoformat()}')
        self.pacer.sleep(seconds)
        self.state.manifest.pop('not_before', None)
        self.state.save()

    def load(self, task):
        if not self.browser:
            self.start()
        url = task['url']
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname != 'www.topcv.vn' or not self.robots.can_fetch(self.ua, url):
            raise SourceStopped(f'Unsupported or robots-disallowed URL: {url}')
        for attempt in range(1, self.max_retries + 2):
            self.retrying_navigation = attempt > 1
            waited = self.pacer.wait()
            record = None
            try:
                response = self._navigate(url)
                self.pacer.finished()
                status = response.status if response else None
                record = self._record(url, status, attempt=attempt,
                                      kind='search', navigation_mode=self.navigation_mode,
                                      response_url=response.url if response else None, pacing_wait_seconds=waited)
                record['clicked_next_url'] = self.clicked_next_url
                record['response_type'] = response.request.resource_type if response else None
                # The short headers property can omit browser-managed headers.
                # Capture the actual request before drawing conclusions about Referer.
                request_headers = response.request.all_headers() if response else {}
                record['request_headers'] = {key: request_headers[key] for key in
                    ['referer', 'sec-fetch-site', 'sec-fetch-mode', 'sec-fetch-user', 'sec-fetch-dest']
                    if key in request_headers}
                headers = response.headers if response else {}
                record['response_headers'] = {key: headers[key] for key in
                    ['cf-ray', 'cf-mitigated', 'server', 'content-type', 'retry-after'] if key in headers}
                if status in {401, 403}:
                    record['access_diagnostic'] = {
                        'via_cloudflare': bool(headers.get('cf-ray')),
                        'challenge_response': headers.get('cf-mitigated') == 'challenge',
                        'navigation_mode': self.navigation_mode,
                        'headless': self.headless,
                    }
                    raise SourceStopped(f'access_blocked: HTTP {status}')
                try:
                    self.page.wait_for_load_state('domcontentloaded', timeout=min(10000, self.timeout_ms))
                except BrowserTimeout:
                    pass
                if self._blocked():
                    raise SourceStopped(f'challenge page: {self.page.title()}')
                if status == 429 or status and status >= 500:
                    if status == 429:
                        self.pacer.slow_down()
                    header = response.headers.get('retry-after', '') if response else ''
                    self._retry(record, attempt, header)
                    continue
                if status != 200:
                    raise RuntimeError(f'HTTP {status}')
                if urlsplit(self.page.url).hostname not in {'www.topcv.vn', 'topcv.vn'} or any(path in self.page.url for path in ['/login', '/dang-nhap']):
                    raise SourceStopped('Redirected away from public search content')
                self.page.wait_for_function(r"""({number, requirePage}) => {
                    const counter = document.querySelector('#job-listing-paginate-text');
                    const match = counter?.textContent.match(/(\d+)\s*\/\s*(\d+)/);
                    if (match && Number(match[1]) !== number) return false;
                    if (requirePage && !match) return false;
                    if (requirePage && window.__topcvListingUpdate &&
                        !window.__topcvListingUpdate.changed &&
                        window.__topcvListingUpdate.root === document.querySelector('.job-list-search-result')) return false;
                    if (document.querySelector('.job-list-search-result .job-item-search-result h3.title a[href]')) return true;
                    const root = document.querySelector('.job-list-search-result');
                    if (!root) return false;
                    const text = root.textContent.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
                    return /khong tim thay (viec lam|tin tuyen dung|ket qua)|khong co (viec lam|tin tuyen dung)( nao)?|no (jobs|results) found/.test(text);
                }""", arg={'number': task['page_number'],
                           'requirePage': self.navigation_mode == 'click_next'}, timeout=self.timeout_ms)
                if not same_listing_page(self.page.url, url):
                    raise SourceStopped('Returned to a different listing page or filter scope')
                if self.navigation_mode == 'click_next':
                    self.page.evaluate('window.__topcvListingUpdate?.observer?.disconnect()')
                html = self.page.content()
                position = list_position(html)
                expected_page = task['page_number']
                reported = position['current_page']
                if reported is not None and reported != expected_page:
                    raise SourceStopped(f'Wrong list page after navigation: expected {expected_page}, observed {reported}')
                self.loaded_list_url = url
                self.loaded_list_number = expected_page
                return html, record['at']
            except BrowserTimeout as exc:
                self.pacer.finished()
                if record is None:
                    record = self._record(url, error='navigation_timeout', attempt=attempt,
                                          kind='search', navigation_mode=self.navigation_mode)
                else:
                    record['error'] = 'content_timeout'
                record.update(clicked_next_url=self.clicked_next_url,
                              browser_url=self.page.url, error_details=str(exc))
                if self._blocked():
                    raise SourceStopped(f'challenge page: {self.page.title()}')
                self._retry(record, attempt)
        raise RuntimeError('Search request did not complete')
