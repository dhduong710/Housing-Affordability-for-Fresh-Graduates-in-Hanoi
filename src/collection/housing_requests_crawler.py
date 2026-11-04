"""Collect Hanoi apartment asking-price listings with requests.Session.

Examples (run from repository root):
    python -B src/collection/housing_requests_crawler.py --pages 5
    python -B src/collection/housing_requests_crawler.py --all --max-pages 20
    python -B src/collection/housing_requests_crawler.py --resume data/raw/RUN_ID
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import requests

from housing_common import (
    BASE, LIST_URL, ROBOTS_URL, attach_detail, discover_last_page,
    load_saved_pages, parse_cards, save_log, save_page, write_outputs,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "Referer": BASE + "/",
}
BLOCK_TITLES = ("just a moment", "attention required", "access denied", "chờ một chút")
TRANSIENT_STATUS = {500, 502, 503, 504}


class AccessBlocked(RuntimeError):
    """The server or robots.txt does not permit this request."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_duration(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs}s"


def session_for_site() -> requests.Session:
    session = requests.Session()
    # This exact header shape was verified on multiple listing pages locally.
    # Clearing defaults also preserves our insertion order.
    session.headers.clear()
    session.headers.update(HEADERS)
    return session


def fetch_html(
    session: requests.Session, url: str, timeout: int, delay: float,
    run_dir: Path, log: dict,
) -> tuple[str, int, float]:
    for attempt in range(1, 4):
        try:
            started = time.monotonic()
            response = session.get(url, timeout=timeout)
            elapsed = round(time.monotonic() - started, 3)
        except requests.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"Request failed at {url}: {exc}") from exc
            log["errors"].append(f"Transient request error at {url}: {exc}")
            save_log(run_dir, log)
            time.sleep(delay * attempt)
            continue

        status = response.status_code
        if status in (401, 403, 429):
            raise AccessBlocked(f"Access blocked (HTTP {status}) at {url}")
        if status in TRANSIENT_STATUS and attempt < 3:
            log["errors"].append(f"Transient HTTP {status} at {url}")
            save_log(run_dir, log)
            time.sleep(delay * attempt)
            continue
        if status != 200:
            raise RuntimeError(f"HTTP {status} at {url}")
        html = response.text
        early_html = html[:8000].lower()
        if any(re.search(r"<title[^>]*>\s*" + re.escape(marker), early_html)
               for marker in BLOCK_TITLES):
            raise AccessBlocked(f"Challenge page returned HTTP 200 at {url}")
        return html, status, elapsed
    raise AssertionError("Unreachable retry state")


def read_robots(
    session: requests.Session, timeout: int, delay: float,
    run_dir: Path, log: dict,
) -> urllib.robotparser.RobotFileParser:
    body, _, _ = fetch_html(session, ROBOTS_URL, timeout, delay, run_dir, log)
    if "<html" in body[:1000].lower():
        raise AccessBlocked("robots.txt returned HTML instead of robots rules")
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(body.splitlines())
    return robots


def reconcile_saved_pages(run_dir: Path, log: dict, completed: set[int]) -> None:
    """Treat atomic page snapshots as authoritative after a crash."""
    entries = {entry["number"]: entry for entry in log.get("pages", [])
               if entry["number"] in completed}
    for page_number in completed - entries.keys():
        page_path = run_dir / "pages" / f"p{page_number}.jsonl"
        with page_path.open(encoding="utf-8") as handle:
            cards = sum(bool(line.strip()) for line in handle)
        entries[page_number] = {
            "number": page_number, "url": LIST_URL.format(page=page_number),
            "http_status": None, "cards": cards, "recovered_from_snapshot": True,
        }
    log["pages"] = [entries[number] for number in sorted(entries)]


def crawl(args: argparse.Namespace) -> tuple[Path, list, dict]:
    if args.resume:
        run_dir = args.resume.resolve()
        log = json.loads((run_dir / "collection_log.json").read_text(encoding="utf-8"))
        if (log.get("schema_version") != "0.3" or log.get("source") != BASE
                or log.get("transport") != "requests.Session"):
            raise RuntimeError("Only runs created by this requests crawler can be resumed")
        if log.get("end_page") is None and (run_dir / "pages" / "p1.jsonl").exists():
            raise RuntimeError("Page 1 was saved before pagination discovery")
        listings, completed = load_saved_pages(run_dir)
        reconcile_saved_pages(run_dir, log, completed)
        args.delay = log.get("delay_seconds", 8.0) if args.delay is None else args.delay
        args.timeout = log.get("timeout_seconds", 30) if args.timeout is None else args.timeout
        args.detail_limit = log.get("detail_limit", 0) if args.detail_limit is None else args.detail_limit
        if "active_elapsed_seconds" not in log and log.get("finished_at_utc"):
            # Runs made before timing was added have only start/end timestamps.
            earlier = datetime.fromisoformat(log["started_at_utc"])
            previous_end = datetime.fromisoformat(log["finished_at_utc"])
            log["active_elapsed_seconds"] = max(0.0, (previous_end - earlier).total_seconds())
            log["active_elapsed_source"] = "estimated_from_prior_timestamps"
        log["status"] = "running"
        log["active_transport"] = "requests.Session"
        log.pop("finished_at_utc", None)
    else:
        args.delay = 8.0 if args.delay is None else args.delay
        args.timeout = 30 if args.timeout is None else args.timeout
        args.detail_limit = 0 if args.detail_limit is None else args.detail_limit
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = args.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        log = {
            "run_id": run_id, "started_at_utc": now(),
            "schema_version": "0.3", "source": BASE,
            "transport": "requests.Session", "active_transport": "requests.Session",
            "start_page": args.start_page,
            "end_page": None if args.all else args.start_page + args.pages - 1,
            "auto_discover_pages": args.all,
            "delay_seconds": args.delay, "timeout_seconds": args.timeout,
            "detail_limit": args.detail_limit, "detail_pages_fetched": 0,
            "robots_url": ROBOTS_URL,
            "active_elapsed_seconds": 0.0,
            "pages": [], "errors": [], "status": "running",
        }
        listings, completed = {}, set()

    save_log(run_dir, log)
    try:
        with session_for_site() as session:
            robots = read_robots(session, args.timeout, args.delay, run_dir, log)
            log["robots_checked_at_utc"] = now()
            save_log(run_dir, log)
            effective_delay = max(args.delay, robots.crawl_delay(USER_AGENT) or 0)
            pages_this_invocation = 0
            page_number = log["start_page"]
            details_blocked = False
            while log["end_page"] is None or page_number <= log["end_page"]:
                if args.max_pages is not None and pages_this_invocation >= args.max_pages:
                    break
                if page_number in completed:
                    page_number += 1
                    continue
                url = LIST_URL.format(page=page_number)
                if not robots.can_fetch(USER_AGENT, url):
                    raise AccessBlocked(f"robots.txt disallows {url}")
                time.sleep(effective_delay)
                html, status, elapsed = fetch_html(
                    session, url, args.timeout, effective_delay, run_dir, log,
                )
                if log["end_page"] is None:
                    log["end_page"] = discover_last_page(html)
                    save_log(run_dir, log)
                    print(f"Discovered {log['end_page']} total pages", flush=True)
                rows = parse_cards(html, url, now())
                if not rows:
                    raise RuntimeError(f"No listing cards at {url}; markup may have changed")

                if args.detail_limit and not details_blocked:
                    for item in rows[:args.detail_limit]:
                        if not robots.can_fetch(USER_AGENT, item.source_url):
                            continue
                        time.sleep(effective_delay)
                        try:
                            detail_html, _, _ = fetch_html(
                                session, item.source_url, args.timeout,
                                effective_delay, run_dir, log,
                            )
                            attach_detail(item, detail_html)
                            if item.listing_id in listings:
                                attach_detail(listings[item.listing_id], detail_html)
                            log["detail_pages_fetched"] = log.get("detail_pages_fetched", 0) + 1
                        except AccessBlocked as exc:
                            log["errors"].append(f"Detail access blocked: {exc}")
                            details_blocked = True
                            break
                        except (requests.RequestException, RuntimeError) as exc:
                            log["errors"].append(f"Detail error: {exc}")

                save_page(run_dir, page_number, rows)
                completed.add(page_number)
                pages_this_invocation += 1
                for item in rows:
                    listings.setdefault(item.listing_id, item)
                log["pages"].append({"number": page_number, "url": url,
                                     "http_status": status, "cards": len(rows),
                                     "fetch_seconds": elapsed})
                log["unique_listings"] = len(listings)
                save_log(run_dir, log)
                print(f"Page {page_number}/{log['end_page']}: {len(rows)} cards, "
                      f"{len(listings)} unique IDs, {elapsed}s", flush=True)
                page_number += 1
            log["status"] = "complete" if page_number > log["end_page"] else "partial"
    except KeyboardInterrupt:
        log["status"] = "interrupted"
        print("Collection interrupted; resume with --resume", file=sys.stderr, flush=True)
    except Exception as exc:
        log["status"] = "blocked" if isinstance(exc, AccessBlocked) else "failed"
        log["errors"].append(str(exc))
        print(f"Collection stopped: {exc}", file=sys.stderr, flush=True)

    log["finished_at_utc"] = now()
    log["unique_listings"] = len(listings)
    save_log(run_dir, log)
    return run_dir, list(listings.values()), log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="Discover and collect all pages")
    parser.add_argument("--max-pages", type=int, help="Stop after N new pages; resume later")
    parser.add_argument("--resume", type=Path, help="Continue from an existing run directory")
    parser.add_argument("--delay", type=float, help="Minimum seconds between requests (default: 8)")
    parser.add_argument("--timeout", type=int, help="Request timeout in seconds (default: 30)")
    parser.add_argument("--detail-limit", type=int, help="Detail pages per listing page (default: 0)")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    if args.start_page < 1 or args.pages < 1 or (args.max_pages is not None and args.max_pages < 1):
        parser.error("start-page/pages/max-pages must be positive")
    if args.delay is not None and args.delay < 2:
        parser.error("--delay must be at least 2 seconds")
    if args.timeout is not None and args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.detail_limit is not None and args.detail_limit < 0:
        parser.error("--detail-limit must be nonnegative")
    if args.all and args.start_page != 1 and not args.resume:
        parser.error("--all starts at page 1")
    if args.resume and (args.all or args.start_page != 1 or args.pages != 1):
        parser.error("Use --resume without --all, --pages or --start-page")
    invocation_started_at = now()
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
    log["finished_at_utc"] = now()
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
