"""Collect Hanoi apartment listings with Playwright (fallback crawler)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

from housing_common import (
    AccessBlocked, BASE, LIST_URL, ROBOTS_URL, USER_AGENT, Listing,
    attach_detail, discover_last_page, format_duration, load_saved_pages,
    parse_cards, save_log, save_page, write_outputs,
)


def read_robots() -> urllib.robotparser.RobotFileParser:
    request = urllib.request.Request(ROBOTS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"robots.txt returned HTTP {response.status}")
        content = response.read().decode("utf-8", errors="replace")
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(content.splitlines())
    return parser


def is_access_block(page, status: int | None) -> bool:
    if status in (401, 403, 429):
        return True
    title = (page.title() or "").lower()
    return any(marker in title for marker in ("just a moment", "chờ một chút", "attention required", "access denied"))


def fetch_page(page, url: str, timeout_ms: int) -> tuple[str, int | None]:
    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    status = response.status if response else None
    if is_access_block(page, status):
        raise AccessBlocked(f"Access blocked (HTTP {status}, title={page.title()!r}) at {url}")
    if status and status >= 400:
        raise RuntimeError(f"HTTP {status} at {url}")
    return page.content(), status


def crawl(args: argparse.Namespace) -> tuple[Path, list[Listing], dict]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    if args.resume:
        run_dir = args.resume.resolve()
        log = json.loads((run_dir / "collection_log.json").read_text(encoding="utf-8"))
        if log.get("schema_version") != "0.3" or log.get("transport") not in (None, "playwright.chromium"):
            raise RuntimeError("Only Playwright runs with a compatible schema can be resumed")
        listings, completed = load_saved_pages(run_dir)
        if log["end_page"] is None and 1 in completed:
            raise RuntimeError("Page 1 was saved before pagination discovery; start a new run")
        if "active_elapsed_seconds" not in log and log.get("finished_at_utc"):
            earlier = datetime.fromisoformat(log["started_at_utc"])
            previous_end = datetime.fromisoformat(log["finished_at_utc"])
            log["active_elapsed_seconds"] = max(0.0, (previous_end - earlier).total_seconds())
            log["active_elapsed_source"] = "estimated_from_prior_timestamps"
        log["status"] = "running"
        log.pop("finished_at_utc", None)
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = args.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        log = {
            "run_id": run_id, "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "schema_version": "0.3", "source": BASE,
            "transport": "playwright.chromium", "active_elapsed_seconds": 0.0,
            "start_page": args.start_page,
            "end_page": None if args.all else args.start_page + args.pages - 1,
            "auto_discover_pages": args.all,
            "delay_seconds": args.delay, "detail_limit": args.detail_limit,
            "robots_url": ROBOTS_URL, "detail_pages_fetched": 0,
            "pages": [], "errors": [], "status": "running",
        }
        listings, completed = {}, set()
        save_log(run_dir, log)

    try:
        robots = read_robots()
        log["robots_checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_log(run_dir, log)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            pages_this_invocation = 0
            page_number = log["start_page"]
            while log["end_page"] is None or page_number <= log["end_page"]:
                if args.max_pages is not None and pages_this_invocation >= args.max_pages:
                    break
                if page_number in completed:
                    page_number += 1
                    continue
                url = LIST_URL.format(page=page_number)
                if not robots.can_fetch(USER_AGENT, url):
                    raise AccessBlocked(f"robots.txt disallows {url}")
                context = browser.new_context(locale="vi-VN", viewport={"width": 1366, "height": 900})
                page = context.new_page()
                try:
                    for attempt in range(1, 3):
                        try:
                            fetch_started = time.monotonic()
                            html, status = fetch_page(page, url, args.timeout * 1000)
                            fetch_seconds = round(time.monotonic() - fetch_started, 3)
                            break
                        except AccessBlocked:
                            raise
                        except (PlaywrightError, RuntimeError) as exc:
                            if attempt == 2:
                                raise
                            log["errors"].append(f"Transient page error at {url}: {exc}")
                            time.sleep(args.delay)
                    if log["end_page"] is None:
                        log["end_page"] = discover_last_page(html)
                        save_log(run_dir, log)
                        print(f"Discovered {log['end_page']} total pages", flush=True)
                    timestamp = datetime.now(timezone.utc).isoformat()
                    rows = parse_cards(html, url, timestamp)
                    if not rows:
                        raise RuntimeError(f"No listing cards found at {url}; site markup may have changed")
                    save_page(run_dir, page_number, rows)
                    completed.add(page_number)
                    pages_this_invocation += 1
                    for item in rows:
                        listings.setdefault(item.listing_id, item)
                    log["pages"].append({"number": page_number, "url": url,
                                         "http_status": status, "cards": len(rows),
                                         "fetch_seconds": fetch_seconds})
                    log["unique_listings"] = len(listings)
                    save_log(run_dir, log)
                    print(f"Page {page_number}/{log['end_page']}: {len(rows)} cards, {len(listings)} unique IDs", flush=True)
                    if args.detail_limit:
                        for item in rows[:args.detail_limit]:
                            if item.detail_text or not robots.can_fetch(USER_AGENT, item.source_url):
                                continue
                            time.sleep(args.delay)
                            try:
                                detail_html, _ = fetch_page(page, item.source_url, args.timeout * 1000)
                                attach_detail(item, detail_html)
                                attach_detail(listings[item.listing_id], detail_html)
                                save_page(run_dir, page_number, rows)
                                log["detail_pages_fetched"] += 1
                            except AccessBlocked:
                                raise
                            except (PlaywrightError, RuntimeError) as exc:
                                log["errors"].append(f"Detail error at {item.source_url}: {exc}")
                finally:
                    context.close()
                page_number += 1
                if page_number <= log["end_page"] and (args.max_pages is None or pages_this_invocation < args.max_pages):
                    time.sleep(args.delay)
            log["status"] = "complete" if page_number > log["end_page"] else "partial"
            browser.close()
    except KeyboardInterrupt:
        log["status"] = "interrupted"
        print("Collection interrupted; resume with --resume", file=sys.stderr, flush=True)
    except Exception as exc:
        log["status"] = "blocked" if isinstance(exc, AccessBlocked) else "failed"
        log["errors"].append(str(exc))
        print(f"Collection stopped: {exc}", file=sys.stderr, flush=True)
    log["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    log["unique_listings"] = len(listings)
    save_log(run_dir, log)
    return run_dir, list(listings.values()), log


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Hanoi apartment asking-price listings.")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="Discover and collect every results page")
    parser.add_argument("--max-pages", type=int, help="Stop after this many new pages; resume later")
    parser.add_argument("--resume", type=Path, help="Continue a run from its output directory")
    parser.add_argument("--delay", type=float, default=8.0, help="Minimum seconds between page requests")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--detail-limit", type=int, default=0, help="Detail pages per results page")
    browser_mode = parser.add_mutually_exclusive_group()
    browser_mode.add_argument("--headed", dest="headless", action="store_false")
    browser_mode.add_argument("--headless", dest="headless", action="store_true")
    parser.set_defaults(headless=False)
    parser.add_argument("--output-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    if args.start_page < 1 or args.pages < 1 or args.delay < 2 or args.detail_limit < 0 or (args.max_pages is not None and args.max_pages < 1):
        parser.error("start-page/pages must be positive; delay >= 2; detail-limit >= 0")
    if args.all and args.start_page != 1 and not args.resume:
        parser.error("--all starts at page 1 to discover the final page")
    invocation_started_at = datetime.now(timezone.utc).isoformat()
    invocation_started = time.monotonic()
    try:
        run_dir, listings, log = crawl(args)
    except Exception as exc:
        print(f"Could not start collection: {exc}", file=sys.stderr)
        return 1
    write_outputs(run_dir, listings, log)
    last_elapsed = round(time.monotonic() - invocation_started, 3)
    log["last_invocation_elapsed_seconds"] = last_elapsed
    log["active_elapsed_seconds"] = round(log.get("active_elapsed_seconds", 0.0) + last_elapsed, 3)
    log["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    log["wall_elapsed_seconds"] = round(
        (datetime.fromisoformat(log["finished_at_utc"]) -
         datetime.fromisoformat(log["started_at_utc"])).total_seconds(), 3
    )
    log.setdefault("invocations", []).append({
        "started_at_utc": invocation_started_at,
        "finished_at_utc": log["finished_at_utc"],
        "elapsed_seconds": last_elapsed,
    })
    save_log(run_dir, log)
    print(f"Run status: {log['status']}; listings: {len(listings)}; output: {run_dir}")
    total_label = ("estimated total active time" if log.get("active_elapsed_source")
                   else "total active time")
    print(f"Elapsed this invocation: {format_duration(last_elapsed)}; "
          f"{total_label}: {format_duration(log['active_elapsed_seconds'])}")
    return 0 if log["status"] in ("complete", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
