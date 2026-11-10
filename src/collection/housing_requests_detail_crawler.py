"""Enrich an existing housing listings CSV with batdongsan.com.vn detail pages.

Run from the repository root:
    python -B src/collection/housing_requests_detail_crawler.py
    python -B src/collection/housing_requests_detail_crawler.py --max-details 50
    python -B src/collection/housing_requests_detail_crawler.py --resume data/detail_full/RUN_ID

The input is frozen at run creation. Per-listing JSON checkpoints permit resume;
the enriched CSV is rebuilt every --flush-every attempts and at each stop.
No listing is discarded, including unavailable or failed detail pages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from housing_common import parse_area, parse_detail, parse_price, save_log
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
AUDIT_FIELDS = (
    "detail_status", "detail_checked_at_utc", "detail_http_status",
    "detail_error",
)
REQUIRED_FIELDS = {
    "listing_id", "source_url", "page_url", "collected_at_utc",
    "price_text", "area_text", "price_vnd", "area_m2",
    "quality_flags", *DETAIL_FIELDS,
}
TERMINAL_STATUSES = {"ok", "not_found"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)) or not REQUIRED_FIELDS.issubset(columns):
            raise ValueError("Input CSV lacks the expected housing columns or has duplicate headers")
        if set(AUDIT_FIELDS) & set(columns):
            raise ValueError("Input must be the listing CSV, not an enriched detail output")
        rows = list(reader)
    if not rows:
        raise ValueError("Input CSV has no listings")
    ids = set()
    for line, row in enumerate(rows, start=2):
        listing_id = row["listing_id"]
        url = urlparse(row["source_url"])
        if not listing_id.isdigit() or listing_id in ids:
            raise ValueError(f"Invalid or duplicate listing_id on CSV line {line}")
        if url.scheme != "https" or url.netloc != "batdongsan.com.vn":
            raise ValueError(f"Unexpected source_url on CSV line {line}")
        if None in row:
            raise ValueError(f"Extra CSV values on line {line}")
        ids.add(listing_id)
    return columns, rows


def load_attempts(run_dir: Path, ids: set[str]) -> dict[str, dict]:
    attempts = {}
    for path in (run_dir / "details").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        listing_id = record.get("listing_id")
        if listing_id not in ids or path.stem != listing_id:
            raise ValueError(f"Unexpected detail checkpoint: {path}")
        attempts[listing_id] = record
    return attempts


def save_attempt(run_dir: Path, record: dict) -> None:
    directory = run_dir / "details"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['listing_id']}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def enriched_row(source: dict[str, str], attempt: dict | None) -> dict[str, str]:
    row = source.copy()
    row.update({field: "" for field in AUDIT_FIELDS})
    row["detail_status"] = "pending"
    if not attempt:
        return row
    row["detail_status"] = attempt["status"]
    row["detail_checked_at_utc"] = attempt.get("checked_at_utc") or ""
    row["detail_http_status"] = str(attempt.get("http_status") or "")
    row["detail_error"] = attempt.get("error") or ""
    if attempt["status"] == "ok":
        for field in DETAIL_FIELDS:
            row[field] = attempt["fields"].get(field) or ""
        flags = set(filter(None, row["quality_flags"].split(";")))
        card_price, detail_price = parse_price(row["price_text"]), parse_price(row["detail_price_text"])
        if card_price and detail_price and abs(card_price - detail_price) / detail_price > 0.01:
            flags.add("card_detail_price_mismatch")
        card_area, detail_area = parse_area(row["area_text"]), parse_area(row["detail_area_text"])
        if card_area and detail_area and abs(card_area - detail_area) / detail_area > 0.02:
            flags.add("card_detail_area_mismatch")
        row["quality_flags"] = ";".join(sorted(flags))
    return row


def write_outputs(
    run_dir: Path, columns: list[str], rows: list[dict[str, str]],
    attempts: dict[str, dict], log: dict,
) -> None:
    target = run_dir / "housing_details_raw.csv"
    temporary = target.with_suffix(".csv.tmp")
    status_counts: Counter[str] = Counter()
    missing = Counter()
    flag_counts = Counter()
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns + list(AUDIT_FIELDS))
        writer.writeheader()
        for source in rows:
            row = enriched_row(source, attempts.get(source["listing_id"]))
            writer.writerow(row)
            status_counts[row["detail_status"]] += 1
            flag_counts.update(filter(None, row["quality_flags"].split(";")))
            if row["detail_status"] == "ok":
                missing.update(field for field in DETAIL_FIELDS if not row[field])
    temporary.replace(target)
    ok_count = status_counts["ok"]
    report = {
        "run_id": log["run_id"],
        "input_csv_sha256": log["input_csv_sha256"],
        "output_csv_sha256": sha256(target),
        "total_listings": len(rows),
        "detail_status_counts": {
            status: status_counts[status] for status in ("ok", "pending", "not_found", "error")
        },
        "detail_field_present_among_ok": {
            field: ok_count - missing[field] for field in DETAIL_FIELDS
        },
        "detail_field_missing_among_ok": {
            field: missing[field] for field in DETAIL_FIELDS
        },
        "quality_flag_counts": dict(sorted(flag_counts.items())),
        "note": "Missing fields are source/selector observations, not automatically invalid listings.",
    }
    report_path = run_dir / "quality_report.json"
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    log["detail_status_counts"] = report["detail_status_counts"]
    save_log(run_dir, log)


def create_run(args: argparse.Namespace) -> tuple[Path, dict]:
    source = args.input.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    # Validate before making a run directory.
    read_source(source)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = (args.output_root / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = run_dir / "source_listings.csv"
    shutil.copyfile(source, snapshot)
    log = {
        "mode": "detail_full", "run_id": run_id, "started_at_utc": now(),
        "original_input_csv": str(source), "input_csv_sha256": sha256(snapshot),
        "delay_seconds": args.delay, "timeout_seconds": args.timeout,
        "status": "running", "errors": [], "active_elapsed_seconds": 0.0,
    }
    save_log(run_dir, log)
    return run_dir, log


def run(args: argparse.Namespace) -> tuple[Path, dict]:
    if args.resume:
        run_dir = args.resume.resolve()
        log = json.loads((run_dir / "collection_log.json").read_text(encoding="utf-8"))
        if log.get("mode") != "detail_full":
            raise ValueError("--resume requires a full-detail run directory")
        delay = log["delay_seconds"] if args.delay is None else args.delay
        timeout = log["timeout_seconds"] if args.timeout is None else args.timeout
        log["delay_seconds"] = delay
        log["timeout_seconds"] = timeout
    else:
        delay = 8.0 if args.delay is None else args.delay
        timeout = 30 if args.timeout is None else args.timeout
        args.delay, args.timeout = delay, timeout
        run_dir, log = create_run(args)
    source = run_dir / "source_listings.csv"
    if sha256(source) != log["input_csv_sha256"]:
        raise ValueError("Frozen source CSV changed; refusing to resume")
    columns, rows = read_source(source)
    attempts = load_attempts(run_dir, {row["listing_id"] for row in rows})
    log["total_listings"] = len(rows)
    log["status"] = "running"
    save_log(run_dir, log)
    print(f"Full detail run: {len(rows)} listings; {len(attempts)} checkpoints; "
          f"output: {run_dir}", flush=True)
    started = time.monotonic()
    started_at = now()
    attempted_this_run = 0
    consecutive_errors = 0
    try:
        with session_for_site() as session:
            robots = read_robots(session, timeout, delay, run_dir, log)
            effective_delay = max(delay, robots.crawl_delay(USER_AGENT) or 0)
            for index, source_row in enumerate(rows, start=1):
                listing_id = source_row["listing_id"]
                previous = attempts.get(listing_id)
                if previous and previous["status"] in TERMINAL_STATUSES:
                    continue
                if args.max_details is not None and attempted_this_run >= args.max_details:
                    break
                url = source_row["source_url"]
                if not robots.can_fetch(USER_AGENT, url):
                    raise AccessBlocked(f"robots.txt disallows {url}")
                print(f"Detail {index}/{len(rows)} (ID {listing_id}): "
                      f"waiting {effective_delay:g}s...", flush=True)
                time.sleep(effective_delay)
                attempted_this_run += 1
                try:
                    html, http_status, fetch_seconds = fetch_html(
                        session, url, timeout, effective_delay, run_dir, log,
                    )
                    fields = parse_detail(html)
                    if not any(fields.values()):
                        raise RuntimeError("No recognized detail fields; check page/selector")
                    record = {
                        "listing_id": listing_id, "source_url": url, "status": "ok",
                        "checked_at_utc": now(), "http_status": http_status,
                        "fetch_seconds": fetch_seconds, "fields": fields,
                    }
                    consecutive_errors = 0
                except AccessBlocked:
                    raise
                except (requests.RequestException, RuntimeError) as exc:
                    message = str(exc)
                    missing_page = message.startswith(("HTTP 404 at ", "HTTP 410 at "))
                    record = {
                        "listing_id": listing_id, "source_url": url,
                        "status": "not_found" if missing_page else "error",
                        "checked_at_utc": now(), "http_status": 404 if "HTTP 404 at " in message else
                        410 if "HTTP 410 at " in message else None,
                        "error": message,
                    }
                    consecutive_errors = 0 if missing_page else consecutive_errors + 1
                    log["errors"].append(f"{listing_id}: {message}")
                save_attempt(run_dir, record)
                attempts[listing_id] = record
                if attempted_this_run % args.flush_every == 0:
                    write_outputs(run_dir, columns, rows, attempts, log)
                print(f"Saved {listing_id}: {record['status']} "
                      f"({len(attempts)}/{len(rows)} attempted)", flush=True)
                if consecutive_errors >= args.max_consecutive_errors:
                    raise RuntimeError(
                        f"{consecutive_errors} consecutive detail errors; stopped for review"
                    )
            log["status"] = ("complete" if all(
                attempts.get(row["listing_id"], {}).get("status") in TERMINAL_STATUSES
                for row in rows
            ) else "partial")
    except KeyboardInterrupt:
        log["status"] = "interrupted"
        print("Interrupted; rerun with --resume", file=sys.stderr, flush=True)
    except AccessBlocked as exc:
        log["status"] = "blocked"
        log["errors"].append(str(exc))
        print(f"Stopped: {exc}", file=sys.stderr, flush=True)
    except Exception as exc:
        log["status"] = "failed"
        log["errors"].append(str(exc))
        print(f"Stopped: {exc}", file=sys.stderr, flush=True)
    elapsed = round(time.monotonic() - started, 3)
    log["last_invocation_elapsed_seconds"] = elapsed
    log["active_elapsed_seconds"] = round(log.get("active_elapsed_seconds", 0) + elapsed, 3)
    log["finished_at_utc"] = now()
    log.setdefault("invocations", []).append({
        "started_at_utc": started_at, "finished_at_utc": log["finished_at_utc"],
        "elapsed_seconds": elapsed, "attempted": attempted_this_run,
    })
    write_outputs(run_dir, columns, rows, attempts, log)
    print(f"Status: {log['status']}; {log['detail_status_counts']}; "
          f"elapsed this run: {format_duration(elapsed)}; output: {run_dir}", flush=True)
    return run_dir, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/housing_listings_raw.csv"))
    parser.add_argument("--resume", type=Path, help="Resume an existing full-detail run")
    parser.add_argument("--output-root", type=Path, default=Path("data/detail_full"))
    parser.add_argument("--max-details", type=int, help="Attempt at most N new detail pages")
    parser.add_argument("--delay", type=float, help="Seconds between requests (default: 8)")
    parser.add_argument("--timeout", type=int, help="Request timeout in seconds (default: 30)")
    parser.add_argument("--flush-every", type=int, default=250,
                        help="Rebuild enriched CSV after N attempts (default: 250)")
    parser.add_argument("--max-consecutive-errors", type=int, default=5)
    args = parser.parse_args()
    if args.max_details is not None and args.max_details < 1:
        parser.error("--max-details must be positive")
    if args.delay is not None and args.delay < 2:
        parser.error("--delay must be at least 2 seconds")
    if args.timeout is not None and args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.flush_every < 1 or args.max_consecutive_errors < 1:
        parser.error("--flush-every and --max-consecutive-errors must be positive")
    if args.resume and args.input != Path("data/housing_listings_raw.csv"):
        parser.error("Use --resume without --input")
    try:
        _, log = run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0 if log["status"] in {"complete", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
