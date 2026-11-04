"""Collect and enrich a small, deduplicated housing detail sample.

Example from the repository root:
    python -B src/collection/housing_detail_sample.py --pages 2

To sample an existing housing CSV instead, add --input data/raw/RUN_ID/housing_raw.csv.

Each successful detail response is saved immediately. Use --resume RUN_DIR to
continue after Ctrl+C or a closed terminal. The source CSV is never modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from housing_crawler import (
    BASE, LIST_URL, Listing, load_saved_pages, parse_area, parse_cards,
    parse_detail, parse_price, save_log, save_page,
)
from housing_requests_crawler import (
    AccessBlocked, USER_AGENT, fetch_html, format_duration, read_robots,
    session_for_site,
)


DETAIL_FIELDS = (
    "project_name", "floor_text", "furniture_text", "legal_status_text",
    "house_direction_text", "balcony_direction_text", "detail_price_text",
    "detail_area_text", "detail_bedrooms_text", "detail_bathrooms_text",
    "detail_text",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_sample(path: Path, pages: int) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        required = {"listing_id", "source_url", "page_url", "price_vnd", "area_m2", "quality_flags"}
        if not required.issubset(columns) or not set(DETAIL_FIELDS).issubset(columns):
            raise RuntimeError("Input CSV does not have the expected housing schema")
        selected = []
        seen = set()
        for row in reader:
            match = re.search(r"/p(\d+)$", row["page_url"])
            if not match or int(match.group(1)) > pages:
                continue
            listing_id = row["listing_id"]
            url = urlparse(row["source_url"])
            if (not listing_id.isdigit() or listing_id in seen or
                    url.scheme != "https" or url.netloc != "batdongsan.com.vn"):
                continue
            selected.append(row)
            seen.add(listing_id)
    if not selected:
        raise RuntimeError(f"No listings from the first {pages} result pages")
    return columns, selected


def collect_listing_sample(
    run_dir: Path, pages: int, delay: float, timeout: int,
    session: requests.Session, robots, log: dict,
) -> Path:
    """Save each listing page, then build one deduplicated CSV for detail work."""
    output = run_dir / "listing_sample.csv"
    if output.exists():
        return output
    for page_number in range(1, pages + 1):
        snapshot = run_dir / "pages" / f"p{page_number}.jsonl"
        if snapshot.exists():
            continue
        url = LIST_URL.format(page=page_number)
        if not robots.can_fetch(USER_AGENT, url):
            raise AccessBlocked(f"robots.txt disallows {url}")
        effective_delay = max(delay, robots.crawl_delay(USER_AGENT) or 0)
        print(f"Listing page {page_number}/{pages}: waiting {effective_delay:g}s, "
              "then fetching...", flush=True)
        time.sleep(effective_delay)
        html, _, _ = fetch_html(session, url, timeout, effective_delay, run_dir, log)
        rows = parse_cards(html, url, now())
        if not rows:
            raise RuntimeError(f"No listing cards at {url}")
        save_page(run_dir, page_number, rows)
        log["listing_pages_completed"] = len(list((run_dir / "pages").glob("p*.jsonl")))
        save_log(run_dir, log)
        print(f"Saved listing page {page_number}/{pages}: {len(rows)} cards", flush=True)

    unique, completed = load_saved_pages(run_dir)
    if completed != set(range(1, pages + 1)):
        raise RuntimeError("Listing-page snapshots are incomplete")
    columns = [field.name for field in fields(Listing)]
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(asdict(item) for item in unique.values())
    temporary.replace(output)
    print(f"Selected {len(unique)} unique listings; saved {output}", flush=True)
    return output


def saved_details(run_dir: Path) -> dict[str, dict]:
    results = {}
    for path in (run_dir / "details").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        results[record["listing_id"]] = record
    return results


def save_detail(run_dir: Path, listing_id: str, record: dict) -> None:
    directory = run_dir / "details"
    directory.mkdir(exist_ok=True)
    path = directory / f"{listing_id}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_sample(run_dir: Path, columns: list[str], rows: list[dict], details: dict[str, dict]) -> None:
    target = run_dir / "housing_detail_sample.csv"
    temporary = target.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for source in rows:
            row = source.copy()
            result = details.get(row["listing_id"])
            if result:
                for field in DETAIL_FIELDS:
                    row[field] = result["fields"].get(field) or ""
                flags = set(filter(None, row["quality_flags"].split(";")))
                detail_price = parse_price(row["detail_price_text"])
                detail_area = parse_area(row["detail_area_text"])
                if row["price_vnd"] and detail_price:
                    if abs(int(row["price_vnd"]) - detail_price) / detail_price > 0.01:
                        flags.add("card_detail_price_mismatch")
                if row["area_m2"] and detail_area:
                    if abs(float(row["area_m2"]) - detail_area) / detail_area > 0.02:
                        flags.add("card_detail_area_mismatch")
                row["quality_flags"] = ";".join(sorted(flags))
            writer.writerow(row)
    temporary.replace(target)


def run(args: argparse.Namespace) -> tuple[Path, dict]:
    if args.resume:
        run_dir = args.resume.resolve()
        log = json.loads((run_dir / "collection_log.json").read_text(encoding="utf-8"))
        if log.get("mode") != "detail_sample":
            raise RuntimeError("--resume requires a detail-sample run directory")
        input_csv = Path(log["input_csv"]) if log.get("input_csv") else None
        pages = log["selected_result_pages"]
        delay = log["delay_seconds"] if args.delay is None else args.delay
        timeout = log["timeout_seconds"] if args.timeout is None else args.timeout
    else:
        input_csv = args.input.resolve() if args.input else None
        pages = args.pages
        delay = 8.0 if args.delay is None else args.delay
        timeout = 30 if args.timeout is None else args.timeout
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = args.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        log = {
            "mode": "detail_sample", "run_id": run_id,
            "started_at_utc": now(),
            "input_csv": str(input_csv) if input_csv else None,
            "source_mode": "existing_csv" if input_csv else "standalone",
            "selected_result_pages": pages, "delay_seconds": delay,
            "timeout_seconds": timeout, "errors": [], "status": "running",
            "active_elapsed_seconds": 0.0,
        }

    columns: list[str] = []
    rows: list[dict[str, str]] = []
    details = saved_details(run_dir)
    log["completed_details"] = len(details)
    log["status"] = "running"
    save_log(run_dir, log)
    print(f"Detail sample from pages 1-{pages}; {len(details)} details already saved; "
          f"output: {run_dir}", flush=True)

    started = time.monotonic()
    started_at = now()
    try:
        with session_for_site() as session:
            robots = read_robots(session, timeout, delay, run_dir, log)
            print("robots.txt checked", flush=True)
            source_csv = input_csv or collect_listing_sample(
                run_dir, pages, delay, timeout, session, robots, log,
            )
            columns, rows = read_sample(source_csv, pages)
            log["selected_listings"] = len(rows)
            save_log(run_dir, log)
            write_sample(run_dir, columns, rows, details)
            print(f"Selected {len(rows)} unique listings; starting detail pages", flush=True)
            effective_delay = max(delay, robots.crawl_delay(USER_AGENT) or 0)
            newly_fetched = 0
            for index, row in enumerate(rows, start=1):
                listing_id = row["listing_id"]
                if listing_id in details:
                    continue
                if args.max_details is not None and newly_fetched >= args.max_details:
                    break
                url = row["source_url"]
                if not robots.can_fetch(USER_AGENT, url):
                    raise AccessBlocked(f"robots.txt disallows {url}")
                print(f"Detail {index}/{len(rows)} (ID {listing_id}): waiting "
                      f"{effective_delay:g}s, then fetching...", flush=True)
                time.sleep(effective_delay)
                try:
                    html, status, fetch_seconds = fetch_html(
                        session, url, timeout, effective_delay, run_dir, log,
                    )
                    record = {"listing_id": listing_id, "source_url": url,
                              "checked_at_utc": now(), "http_status": status,
                              "fetch_seconds": fetch_seconds,
                              "fields": parse_detail(html)}
                    save_detail(run_dir, listing_id, record)
                    details[listing_id] = record
                    newly_fetched += 1
                    log["completed_details"] = len(details)
                    save_log(run_dir, log)
                    write_sample(run_dir, columns, rows, details)
                    print(f"Saved detail {len(details)}/{len(rows)} (ID {listing_id}); "
                          f"project={bool(record['fields']['project_name'])}, "
                          f"description={bool(record['fields']['detail_text'])}", flush=True)
                except AccessBlocked:
                    raise
                except (requests.RequestException, RuntimeError) as exc:
                    log["errors"].append(f"{listing_id}: {exc}")
                    save_log(run_dir, log)
                    print(f"Detail {listing_id} failed: {exc}", file=sys.stderr, flush=True)
            log["status"] = "complete" if len(details) == len(rows) else "partial"
    except KeyboardInterrupt:
        log["status"] = "interrupted"
        print("Interrupted; rerun with --resume and this output directory", file=sys.stderr, flush=True)
    except Exception as exc:
        log["status"] = "blocked" if isinstance(exc, AccessBlocked) else "failed"
        log["errors"].append(str(exc))
        print(f"Stopped: {exc}", file=sys.stderr, flush=True)

    elapsed = round(time.monotonic() - started, 3)
    log["last_invocation_elapsed_seconds"] = elapsed
    log["active_elapsed_seconds"] = round(log.get("active_elapsed_seconds", 0.0) + elapsed, 3)
    log["finished_at_utc"] = now()
    log.setdefault("invocations", []).append({
        "started_at_utc": started_at, "finished_at_utc": log["finished_at_utc"],
        "elapsed_seconds": elapsed,
    })
    save_log(run_dir, log)
    if columns and rows:
        write_sample(run_dir, columns, rows, details)
    print(f"Status: {log['status']}; saved: {len(details)}/{len(rows)}; "
          f"elapsed: {format_duration(elapsed)}; output: {run_dir}", flush=True)
    return run_dir, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Optional existing housing_raw.csv; omit to crawl listing pages")
    parser.add_argument("--pages", type=int, default=2, help="First N result pages (default: 2)")
    parser.add_argument("--resume", type=Path, help="Continue a previous detail-sample run")
    parser.add_argument("--max-details", type=int, help="Fetch at most N new details this time")
    parser.add_argument("--delay", type=float, help="Seconds between requests (default: 8)")
    parser.add_argument("--timeout", type=int, help="Request timeout in seconds (default: 30)")
    parser.add_argument("--output-root", type=Path, default=Path("data/detail_sample"))
    args = parser.parse_args()
    if args.pages < 1 or (args.max_details is not None and args.max_details < 1):
        parser.error("--pages and --max-details must be positive")
    if args.delay is not None and args.delay < 2:
        parser.error("--delay must be at least 2 seconds")
    if args.timeout is not None and args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.resume and args.input:
        parser.error("Use --resume without --input")
    try:
        _, log = run(args)
    except Exception as exc:
        print(f"Could not start detail sample: {exc}", file=sys.stderr)
        return 1
    return 0 if log["status"] in ("complete", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
